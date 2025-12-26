import json
import subprocess
import sys
from collections import defaultdict

# ===== 配置區段 =====
# 排除的 Namespace (系統層級服務)
EXCLUDED_NAMESPACES = [
    "kube-system",
    "gke-managed-system",
    "istio-system",
    "gmp-system",
    "gke-gmp-system",
    "gke-managed-cim",
    "gke-managed-dpv2-observability",
]

# 決策閾值配置
THRESHOLDS = {
    "mem_upgrade": 80,  # 記憶體使用率 >80% 且 CPU <40% 時升級機型
    "cpu_upgrade": 80,  # CPU 使用率 >80% 且記憶體 <40% 時升級機型
    "scale_out": 75,  # 兩者都 >75% 時擴充節點
    "scale_in": 20,  # 兩者都 <20% 時縮減節點
    "cpu_crossover": 40,  # CPU 閒置閾值
    "mem_crossover": 40,  # 記憶體閒置閾值
    "pod_limit_critical": 85,  # Pod 達到 Limit 的 85% 時告警
    "pod_burst_critical": 100,  # Pod 超過 Request 的 100% 時告警
    "node_oom_danger": 90,  # Node 記憶體 >90% 時 OOM 危險
    "node_high_load": 80,  # Node 記憶體 >80% 時高負載
}

# Kubectl 命令配置
KUBECTL_TIMEOUT = 30
KUBECTL_RETRY_COUNT = 1


# ===== 輔助函式 =====
def run_command(command, timeout=KUBECTL_TIMEOUT):
    """
    執行 Shell 命令，帶有超時和錯誤處理

    Args:
        command: Shell 命令字串
        timeout: 超時時間（秒）

    Returns:
        命令輸出字串，失敗時返回空字串並打印警告
    """
    try:
        result = subprocess.run(
            command,
            shell=True,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            text=True,
        )
        return result.stdout
    except subprocess.TimeoutExpired:
        print(f"⚠️  警告: 命令超時 (>{timeout}s): {command}")
        return ""
    except subprocess.CalledProcessError as e:
        print(f"⚠️  警告: 命令失敗: {command}")
        if e.stderr:
            print(f"        {e.stderr.strip()}")
        return ""
    except Exception as e:
        print(f"⚠️  警告: 執行命令時出錯: {e}")
        return ""


def parse_cpu(value):
    """
    解析 CPU 值為核心數

    支援格式: 1 (核心), 100m (毫核), 100n (納核)
    """
    val_str = str(value).strip()
    if not val_str:
        return 0.0

    try:
        if val_str.endswith("m"):
            return float(val_str[:-1]) / 1000
        elif val_str.endswith("n"):
            return float(val_str[:-1]) / 1_000_000_000
        else:
            return float(val_str)
    except (ValueError, IndexError):
        return 0.0


def parse_memory(value):
    """
    解析記憶體值為 Mi (Mebibytes)

    支援格式: 100Mi, 1Gi, 1Ki, 1Ti, 1024 (bytes)
    """
    s_val = str(value).strip()
    if not s_val:
        return 0.0

    units = {
        "Ti": 1024 * 1024,
        "Gi": 1024,
        "Mi": 1,
        "Ki": 1 / 1024,
    }

    try:
        for unit, multiplier in units.items():
            if s_val.endswith(unit):
                return float(s_val[: -len(unit)]) * multiplier

        # 沒有單位時視為 bytes，轉為 Mi
        if s_val.isdigit():
            return float(s_val) / (1024 * 1024)

        return float(s_val)
    except (ValueError, IndexError):
        return 0.0


def suggest_upgrade_type(current_type, action):
    """
    根據當前機型給出具體的升級建議

    例如: n2-standard-2 -> n2-highmem-2 (升級記憶體)
    """
    if not current_type or "unknown" in current_type:
        return ""

    parts = current_type.split("-")
    if len(parts) < 3:
        return ""

    family = parts[0]  # e.g. n2d
    series = parts[1]  # e.g. standard
    cores = parts[2]  # e.g. 2

    if action == "UPGRADE_MEM":
        # standard -> highmem 或 highcpu -> standard
        new_series = "highmem" if series == "standard" else "standard"
        return f" → {family}-{new_series}-{cores}"
    elif action == "UPGRADE_CPU":
        # standard -> highcpu 或 highmem -> standard
        new_series = "highcpu" if series == "standard" else "standard"
        return f" → {family}-{new_series}-{cores}"

    return ""


def analyze_pool_health(pool_name, nodes):
    """
    分析整個 Pool 的平均負載並給出架構建議

    Returns:
        包含分析結果的字典，或 None 如果數據不足
    """
    if not nodes:
        return None

    total_cpu_alloc = sum(n["cpu_alloc"] for n in nodes)
    total_mem_alloc = sum(n["mem_alloc"] for n in nodes)
    total_cpu_use = sum(n["cpu_use"] for n in nodes)
    total_mem_use = sum(n["mem_use"] for n in nodes)
    current_type = nodes[0]["type"] if nodes else "unknown"

    # 避免除以零
    if total_cpu_alloc == 0 or total_mem_alloc == 0:
        return None

    avg_cpu_pct = (total_cpu_use / total_cpu_alloc) * 100
    avg_mem_pct = (total_mem_use / total_mem_alloc) * 100

    # 架構師決策邏輯
    recommendation = "✅ 維持現狀 (Keep)"
    action_type = "NONE"
    upgrade_hint = ""

    # 1. 記憶體嚴重不足但 CPU 閒置 -> 換機型到高記憶體
    if (
        avg_mem_pct > THRESHOLDS["mem_upgrade"]
        and avg_cpu_pct < THRESHOLDS["cpu_crossover"]
    ):
        action_type = "UPGRADE_MEM"
        upgrade_hint = suggest_upgrade_type(current_type, action_type)
        recommendation = f"🔧 升級機型 (HighMem){upgrade_hint}"

    # 2. CPU 嚴重不足但記憶體閒置 -> 換機型到高 CPU
    elif (
        avg_cpu_pct > THRESHOLDS["cpu_upgrade"]
        and avg_mem_pct < THRESHOLDS["mem_crossover"]
    ):
        action_type = "UPGRADE_CPU"
        upgrade_hint = suggest_upgrade_type(current_type, action_type)
        recommendation = f"🔧 升級機型 (HighCPU){upgrade_hint}"

    # 3. 兩者都高 -> 擴充節點
    elif avg_mem_pct > THRESHOLDS["scale_out"] or avg_cpu_pct > THRESHOLDS["scale_out"]:
        recommendation = "📦 擴充節點 (Scale Out)"
        action_type = "SCALE_OUT"

    # 4. 兩者都極低 -> 縮減節點
    elif avg_mem_pct < THRESHOLDS["scale_in"] and avg_cpu_pct < THRESHOLDS["scale_in"]:
        recommendation = "📉 縮減節點 (Scale In)"
        action_type = "SCALE_IN"

    return {
        "pool": pool_name,
        "type": current_type,
        "nodes": len(nodes),
        "avg_cpu": avg_cpu_pct,
        "avg_mem": avg_mem_pct,
        "rec": recommendation,
        "action": action_type,
    }


def parse_pod_risks(pods_data, pod_usage):
    """
    分析 Pod 風險並返回高風險 Pod 清單

    Returns:
        高風險 Pod 列表
    """
    risky_pods = []

    for pod in pods_data.get("items", []):
        # 只檢查運行中的 Pod
        if pod.get("status", {}).get("phase") != "Running":
            continue

        ns = pod["metadata"].get("namespace")
        if ns in EXCLUDED_NAMESPACES:
            continue

        name = pod["metadata"].get("name")
        if not name:
            continue

        # 計算 Pod 內所有 Container 的 Request/Limit 總和
        containers = pod.get("spec", {}).get("containers", [])
        mem_req = sum(
            parse_memory(c.get("resources", {}).get("requests", {}).get("memory", "0"))
            for c in containers
        )
        mem_lim = sum(
            parse_memory(c.get("resources", {}).get("limits", {}).get("memory", "0"))
            for c in containers
        )

        # 從 metrics 中獲取實際使用量
        usage = pod_usage.get((ns, name), {}).get("mem", 0.0)

        # 檢測風險
        risk_msg = ""
        if mem_lim > 0 and (usage / mem_lim) > (THRESHOLDS["pod_limit_critical"] / 100):
            risk_msg = f"⚠️ Limit 告急 ({usage / mem_lim * 100:.0f}%)"
        elif mem_req > 0 and (usage / mem_req) > (
            THRESHOLDS["pod_burst_critical"] / 100
        ):
            risk_msg = f"⚠️ 超賣 Burst ({usage / mem_req * 100:.0f}%)"

        if risk_msg:
            risky_pods.append([ns, name, usage, mem_lim, risk_msg])

    return risky_pods


# ===== 主程式 =====
def gke_decision_maker():
    """GKE 架構決策分析主程式"""
    print("正在進行 GKE 架構決策分析... 請稍候\n")

    # 1. 抓取資料
    print("📥 正在收集 Kubernetes 資料...\n")

    nodes_json = run_command("kubectl get nodes -o json")
    if not nodes_json:
        print("❌ 無法取得 Node 資訊，請檢查 kubectl 連線。")
        sys.exit(1)

    try:
        nodes_data = json.loads(nodes_json)
    except json.JSONDecodeError as e:
        print(f"❌ 解析 Node JSON 失敗: {e}")
        sys.exit(1)

    # 嘗試抓取 Metrics (非關鍵，失敗時繼續)
    node_metrics = run_command("kubectl top nodes --no-headers")
    pod_metrics = run_command("kubectl top pods -A --no-headers")

    # 解析 Node Metrics
    node_usage = {}
    if node_metrics:
        for line in node_metrics.strip().split("\n"):
            if not line.strip():
                continue
            parts = line.split()
            if len(parts) >= 5:
                node_usage[parts[0]] = {
                    "cpu": parse_cpu(parts[1]),
                    "mem": parse_memory(parts[3]),
                }

    # 解析 Pod Metrics
    pod_usage = {}
    if pod_metrics:
        for line in pod_metrics.strip().split("\n"):
            if not line.strip():
                continue
            parts = line.split()
            if len(parts) >= 4:
                pod_usage[(parts[0], parts[1])] = {
                    "cpu": parse_cpu(parts[2]),
                    "mem": parse_memory(parts[3]),
                }

    # 整合 Node 資料
    pools = defaultdict(list)
    node_map = {}

    for node in nodes_data.get("items", []):
        name = node.get("metadata", {}).get("name")
        if not name:
            continue

        labels = node.get("metadata", {}).get("labels", {})
        pool = labels.get("cloud.google.com/gke-nodepool", "default")
        m_type = labels.get("node.kubernetes.io/instance-type", "unknown")
        alloc = node.get("status", {}).get("allocatable", {})

        info = {
            "name": name,
            "pool": pool,
            "type": m_type,
            "cpu_alloc": parse_cpu(alloc.get("cpu", "0")),
            "mem_alloc": parse_memory(alloc.get("memory", "0")),
            "cpu_use": node_usage.get(name, {}).get("cpu", 0.0),
            "mem_use": node_usage.get(name, {}).get("mem", 0.0),
        }
        pools[pool].append(info)
        node_map[name] = info

    # 取得 Pod 資料
    pods_json = run_command("kubectl get pods --all-namespaces -o json")
    pods_data = {}
    if pods_json:
        try:
            pods_data = json.loads(pods_json)
        except json.JSONDecodeError as e:
            print(f"⚠️  警告: 解析 Pod JSON 失敗: {e}")
            pods_data = {}

    risky_pods = parse_pod_risks(pods_data, pod_usage)

    # ===== 輸出報告 1: Pool 決策儀表板 =====
    print("=" * 110)
    print("📊 GKE 決策儀表板 (Pool Decision Dashboard)")
    print("   目標: 識別是否需要 [更換機型] 或 [擴充數量]")
    print("-" * 110)
    print(
        f"{'POOL NAME':<20} | {'TYPE':<18} | {'AVG CPU':<8} | {'AVG MEM':<8} | {'RECOMMENDATION'}"
    )

    for pool_name, nodes in pools.items():
        stats = analyze_pool_health(pool_name, nodes)
        if not stats:
            continue

        cpu_str = f"{stats['avg_cpu']:.0f}%"
        mem_str = f"{stats['avg_mem']:.0f}%"

        # 顏色標示
        rec_str = stats["rec"]
        if "升級" in rec_str or "擴充" in rec_str:
            rec_str = f"\033[91m{rec_str}\033[0m"  # 紅色高亮

        print(
            f"{pool_name:<20} | {stats['type']:<18} | {cpu_str:<8} | {mem_str:<8} | {rec_str}"
        )

    print("=" * 110)
    print("")

    # ===== 輸出報告 2: 詳細節點數據 =====
    print("=" * 110)
    print("🔍 節點詳細數據 (Node Inspection)")
    print("-" * 110)
    print(
        f"{'NODE NAME':<25} | {'POOL':<15} | {'CPU USE':<8} | {'MEM USE':<8} | {'STATUS'}"
    )

    for pool_name in sorted(pools.keys()):
        nodes = sorted(pools[pool_name], key=lambda x: x["name"])
        for node in nodes:
            c_pct = (
                (node["cpu_use"] / node["cpu_alloc"] * 100)
                if node["cpu_alloc"] > 0
                else 0
            )
            m_pct = (
                (node["mem_use"] / node["mem_alloc"] * 100)
                if node["mem_alloc"] > 0
                else 0
            )

            status = "🟢"
            if m_pct > THRESHOLDS["node_oom_danger"]:
                status = "🔴 OOM危險"
            elif m_pct > THRESHOLDS["node_high_load"]:
                status = "🟠 高負載"

            # 智慧縮短名稱
            short_name = node["name"].split("-")[-1]
            if len(short_name) < 5:
                short_name = "-".join(node["name"].split("-")[-2:])

            print(
                f"...{short_name:<22} | {pool_name:<15} | {c_pct:<8.0f}% | {m_pct:<8.0f}% | {status}"
            )

    print("=" * 110)
    print("")

    # ===== 輸出報告 3: 風險 Pod =====
    if risky_pods:
        print("=" * 110)
        print("🔥 高風險 Pod 清單 (High Risk Pods) - 已排除系統服務")
        print("-" * 110)
        print(f"{'NAMESPACE':<20} | {'POD NAME':<30} | {'MEM USE':<10} | {'RISK TYPE'}")

        # 依風險程度排序
        risky_pods.sort(key=lambda x: x[2], reverse=True)

        for p in risky_pods[:15]:  # 列出前 15 名
            ns_short = p[0] if len(p[0]) < 20 else p[0][:17] + "..."
            p_name_short = p[1] if len(p[1]) < 30 else "..." + p[1][-27:]
            print(f"{ns_short:<20} | {p_name_short:<30} | {p[2]:<8.0f}Mi | {p[4]}")

        print("=" * 110)
    else:
        print("✅ 沒有偵測到屬於您應用程式的高風險 Pod (系統服務已自動排除)。")

    print("\n💡 架構師行動指南 (Action Items):")
    print("   1. [🔧 升級機型]: 請建立新的 Node Pool，並將 Pod 遷移過去。")
    print("      -> 指令參考: gcloud container node-pools create highmem-pool \\")
    print("                  --machine-type=n2-highmem-4 --zone=us-central1-a ...\n")
    print("   2. [📦 擴充節點]: 使用 GKE Autopilot 或手動擴展 Node Pool。")
    print("      -> 指令參考: gcloud container node-pools update <pool-name> \\")
    print("                  --enable-autoscaling --min-nodes 2 --max-nodes 10\n")
    print("   3. [⚠️ 超賣 Burst]: 修改 Pod YAML，調高 requests.memory。")
    print("      -> 這能防止 K8s 將過多 Pod 塞在同一台機器上。\n")


if __name__ == "__main__":
    gke_decision_maker()
