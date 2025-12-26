# GKE 資源檢查工具操作手冊

## 📋 目錄
1. [工具簡介](#工具簡介)
2. [系統需求](#系統需求)
3. [安裝與配置](#安裝與配置)
4. [快速開始](#快速開始)
5. [報告解讀](#報告解讀)
6. [決策指南](#決策指南)
7. [配置調整](#配置調整)
8. [常見問題](#常見問題)
9. [故障排除](#故障排除)

---

## 工具簡介

### 什麼是 GKE 決策工具？

`gke_resource_check.py` 是一個自動化的 Google Kubernetes Engine (GKE) 架構分析工具，它會：

- 📊 **收集資源數據**：從 Kubernetes 集群中提取節點和 Pod 資源配置
- 🔍 **分析負載情況**：計算 CPU 和記憶體的使用率
- 🎯 **提供決策建議**：根據預設的閾值，自動識別是否需要升級機型或擴充節點
- 🚨 **監測高風險 Pod**：識別可能導致 OOM 或資源超賣的 Pod

### 主要功能

| 功能 | 說明 |
|------|------|
| **Pool 決策儀表板** | 顯示每個 Node Pool 的平均 CPU/記憶體使用率及建議行動 |
| **節點詳細數據** | 列出每個節點的實時資源使用情況和風險狀態 |
| **高風險 Pod 清單** | 識別超出資源限制或過度超賣的 Pod，協助優化 |

---

## 系統需求

### 必要工具

- **Python 3.6+**（支援 JSON 和 subprocess 模組）
- **kubectl**（v1.18 或更新版本）
  - 需要配置正確的 kubeconfig 指向目標 GKE 集群
- **Google Cloud SDK**（可選，如需執行 gcloud 命令）

### 權限要求

執行此工具需要以下 Kubernetes 權限：

```yaml
- "get" on nodes
- "get" on pods (all namespaces)
- "get" on metrics.k8s.io
```

通常情況下，具有以下角色的使用者可以執行此工具：

- `roles/container.admin`（完整管理員）
- `roles/container.viewer`（檢視者）+ metrics 讀取權限

### 網路要求

- 能連接到 GKE 集群的 Kubernetes API Server
- 集群已安裝 Metrics Server（用於收集資源使用數據）

---

## 安裝與配置

### 1. 檢查 Python 環境

```bash
python3 --version
# 預期輸出: Python 3.6 或更新版本
```

### 2. 驗證 kubectl 連線

```bash
kubectl cluster-info
kubectl auth can-i get pods --all-namespaces
```

如果輸出均為 `yes`，表示權限配置正確。


---

## 快速開始

### 執行工具

```bash
python3 gke_resource_check.py
```

### 預期輸出格式

工具會產生三份報告：

1. **GKE 決策儀表板** - 池層級的摘要分析
2. **節點詳細數據** - 每個節點的資源狀況
3. **高風險 Pod 清單** - 需要關注的應用程式

### 執行時間

- 小型集群（<10 節點）：5-10 秒
- 中型集群（10-50 節點）：15-30 秒
- 大型集群（>50 節點）：30-60 秒

---

## 報告解讀

### 報告 1：GKE 決策儀表板

```
📊 GKE 決策儀表板 (Pool Decision Dashboard)
   目標: 識別是否需要 [更換機型] 或 [擴充數量]
────────────────────────────────────────────────────────────────────────────
POOL NAME        | TYPE              | AVG CPU  | AVG MEM  | RECOMMENDATION
────────────────────────────────────────────────────────────────────────────
default-pool     | n2-standard-4     | 45%      | 72%      | 🔧 升級機型 (HighMem) → n2-highmem-4
compute-pool     | n2-standard-8     | 82%      | 78%      | 📦 擴充節點 (Scale Out)
```

#### 欄位說明

| 欄位 | 說明 |
|------|------|
| **POOL NAME** | Node Pool 的名稱 |
| **TYPE** | 機器類型，格式為 `<系列>-<類型>-<核心數>`（例：n2-standard-4） |
| **AVG CPU** | 該 Pool 中所有節點的平均 CPU 使用率 |
| **AVG MEM** | 該 Pool 中所有節點的平均記憶體使用率 |
| **RECOMMENDATION** | 架構師建議行動 |

#### 建議行動解釋

| 符號 | 行動 | 含義 | 原因 |
|------|------|------|------|
| ✅ | 維持現狀 | 資源均衡 | CPU 和記憶體使用率都在合理範圍內 |
| 🔧 | 升級機型 (HighMem) | 更換為高記憶體機器 | 記憶體 >80% 但 CPU <40%（記憶體成為瓶頸） |
| 🔧 | 升級機型 (HighCPU) | 更換為高 CPU 機器 | CPU >80% 但記憶體 <40%（CPU 成為瓶頸） |
| 📦 | 擴充節點 | 增加更多節點到 Pool | CPU 或記憶體 >75%（資源即將耗盡） |
| 📉 | 縮減節點 | 減少 Pool 中的節點數 | CPU 和記憶體都 <20%（資源未充分利用） |

### 報告 2：節點詳細數據

```
🔍 節點詳細數據 (Node Inspection)
─────────────────────────────────────────────────────────────────────────────
NODE NAME                | POOL           | CPU USE  | MEM USE  | STATUS
─────────────────────────────────────────────────────────────────────────────
...gke-node-1            | default-pool   | 55%      | 68%      | 🟢
...gke-node-2            | default-pool   | 62%      | 85%      | 🟠 高負載
...gke-node-3            | default-pool   | 45%      | 92%      | 🔴 OOM危險
```

#### 狀態指示器

| 符號 | 狀態 | 含義 | 行動 |
|------|------|------|------|
| 🟢 | 健康 | 記憶體使用率 <80% | 無需立即行動 |
| 🟠 | 高負載 | 80% ≤ 記憶體使用率 ≤ 90% | 監測，準備擴充 |
| 🔴 | OOM危險 | 記憶體使用率 >90% | 立即採取行動 |

### 報告 3：高風險 Pod 清單

```
🔥 高風險 Pod 清單 (High Risk Pods) - 已排除系統服務
────────────────────────────────────────────────────────────────────────────
NAMESPACE       | POD NAME                      | MEM USE    | RISK TYPE
────────────────────────────────────────────────────────────────────────────
production      | api-server-deploy-7f9c8b2a1  | 2048 Mi    | ⚠️ Limit 告急 (92%)
staging         | cache-worker-5d4e9f1b        | 1024 Mi    | ⚠️ 超賣 Burst (145%)
```

#### 風險類型解釋

| 風險類型 | 告警級別 | 解決方案 |
|---------|---------|---------|
| **⚠️ Limit 告急** | 高 | Pod 記憶體使用已達到 Limit 的 85%，容易 OOM。修改 YAML 增加 `limits.memory` |
| **⚠️ 超賣 Burst** | 中 | Pod 記憶體超過 Request，系統可能將過多 Pod 調度到同一節點。增加 `requests.memory` |

---

## 決策指南

### 場景 1：記憶體瓶頸（升級機型到 HighMem）

**症狀：**
- AVG MEM > 80%
- AVG CPU < 40%
- 節點上有 Pod 接近記憶體 Limit

**行動步驟：**

1. 建立新的高記憶體 Node Pool

```bash
gcloud container node-pools create highmem-pool \
  --cluster=<cluster-name> \
  --zone=<zone> \
  --machine-type=n2-highmem-4 \
  --enable-autoscaling \
  --min-nodes=2 \
  --max-nodes=10 \
  --disk-size=100
```

2. 添加 Pod 親和性標籤（可選，用於控制 Pod 調度）

```yaml
nodeSelector:
  cloud.google.com/gke-nodepool: highmem-pool
```

3. 監測新節點上的工作負載遷移

```bash
kubectl get nodes -l cloud.google.com/gke-nodepool=highmem-pool
```

4. 確認穩定後，可考慮縮減舊 Pool

```bash
gcloud container node-pools update default-pool \
  --enable-autoscaling \
  --min-nodes=1 \
  --max-nodes=3
```

### 場景 2：CPU 瓶頸（升級機型到 HighCPU）

**症狀：**
- AVG CPU > 80%
- AVG MEM < 40%
- 節點上有 CPU 密集型的工作負載

**行動步驟：**

1. 建立新的高 CPU Node Pool

```bash
gcloud container node-pools create highcpu-pool \
  --cluster=<cluster-name> \
  --zone=<zone> \
  --machine-type=n2-highcpu-8 \
  --enable-autoscaling \
  --min-nodes=2 \
  --max-nodes=10
```

2. 為 CPU 密集型應用添加節點親和性

```yaml
nodeSelector:
  workload-type: cpu-intensive
```

3. 在新節點上應用此標籤

```bash
gcloud compute instances add-labels <node-name> \
  --labels=workload-type=cpu-intensive \
  --zone=<zone>
```

### 場景 3：資源不足（擴充節點）

**症狀：**
- AVG CPU > 75% 或 AVG MEM > 75%
- 無法通過升級機型解決（兩種資源都緊張）
- 新 Pod 無法成功調度（pending 狀態）

**行動步驟：**

1. 啟用或更新 Node Pool 自動擴展

```bash
gcloud container node-pools update <pool-name> \
  --enable-autoscaling \
  --min-nodes=<current-size> \
  --max-nodes=<current-size * 2>
```

2. 驗證自動擴展是否啟用

```bash
gcloud container node-pools describe <pool-name> \
  --cluster=<cluster-name> \
  --format="value(autoscaling)"
```

3. 監測擴展進度

```bash
kubectl get nodes -w
# 觀察新節點加入
```

### 場景 4：資源浪費（縮減節點）

**症狀：**
- AVG CPU < 20% 且 AVG MEM < 20%
- 多個節點使用率極低
- 集群成本持續上升

**行動步驟：**

1. 調整自動擴展的最小節點數

```bash
gcloud container node-pools update <pool-name> \
  --enable-autoscaling \
  --min-nodes=2 \
  --max-nodes=5
```

2. Kubernetes 會自動驅逐低利用率節點上的 Pod

```bash
# 監測 Pod 重新調度
kubectl get pods -A -w | grep -E "(Pending|Running)"
```

3. 確認節點已被移除

```bash
gcloud compute instances list --filter="zone:<zone>" | grep <pool-name>
```

### 場景 5：高風險 Pod 的超賣（Burst）

**症狀：**
- Pod 記憶體使用量超過 Request（如 Request: 512Mi，實際: 768Mi）
- 風險提示：⚠️ 超賣 Burst (145%)

**行動步驟：**

1. 檢查 Pod 的當前配置

```bash
kubectl get pod <pod-name> -n <namespace> -o yaml | grep -A 5 "resources:"
```

2. 修改 Pod 的 Request 值

```yaml
resources:
  requests:
    memory: "768Mi"      # 提高 Request
    cpu: "250m"
  limits:
    memory: "1Gi"        # 保持 Limit
    cpu: "500m"
```

3. 滾動更新應用

```bash
kubectl rollout restart deployment/<deployment-name> -n <namespace>
```

4. 驗證調整效果

```bash
# 等待 30 秒後重新執行工具
python3 gke_resource_check.py
```

---

## 配置調整

### 修改決策閾值

編輯 `gke_resource_check.py` 中的 `THRESHOLDS` 字典以適應你的業務需求：

```python
THRESHOLDS = {
    "mem_upgrade": 80,              # 記憶體升級閾值（預設 80%）
    "cpu_upgrade": 80,              # CPU 升級閾值（預設 80%）
    "scale_out": 75,                # 擴充節點閾值（預設 75%）
    "scale_in": 20,                 # 縮減節點閾值（預設 20%）
    "cpu_crossover": 40,            # CPU 閒置閾值（預設 40%）
    "mem_crossover": 40,            # 記憶體閒置閾值（預設 40%）
    "pod_limit_critical": 85,       # Pod Limit 告警（預設 85%）
    "pod_burst_critical": 100,      # Pod 超賣告警（預設 100%）
    "node_oom_danger": 90,          # OOM 危險閾值（預設 90%）
    "node_high_load": 80,           # 高負載閾值（預設 80%）
}
```

### 排除特定 Namespace

編輯 `EXCLUDED_NAMESPACES` 列表以排除系統或非業務相關的 Pod：

```python
EXCLUDED_NAMESPACES = [
    "kube-system",
    "gke-managed-system",
    "istio-system",
    "gmp-system",
    "my-system-namespace",  # 自訂排除
]
```

### 調整 kubectl 超時

如果集群較大或網路較慢，可增加超時時間：

```python
KUBECTL_TIMEOUT = 60  # 從 30 秒增加到 60 秒
```

---

## 常見問題

### Q1：工具顯示「⚠️ 警告: 命令失敗」？

**原因：** kubectl 命令執行失敗，通常是 kubeconfig 配置問題。

**解決方案：**

```bash
# 檢查 kubeconfig
kubectl config current-context

# 如果不正確，切換到正確的集群
kubectl config use-context <cluster-context>

# 驗證連線
kubectl cluster-info
```

### Q2：為什麼 Pod 清單是空的？

**原因：** 可能是 Metrics Server 未安裝或 Pod 都在排除的 Namespace 中。

**解決方案：**

```bash
# 檢查 Metrics Server
kubectl get deployment metrics-server -n kube-system

# 檢查是否有非系統 Pod
kubectl get pods -A --exclude-namespaces=kube-system,istio-system,gke-managed-system
```

### Q3：「升級機型」和「擴充節點」如何選擇？

**使用決策樹：**

1. 檢查 AVG CPU 和 AVG MEM
2. 如果**只有一種資源**使用率高（>80%），另一種低（<40%）→ **升級機型**
3. 如果**兩種資源**都高（>75%）→ **擴充節點**
4. 如果**兩種資源**都低（<20%）→ **縮減節點**

### Q4：為什麼建議「升級機型 (HighMem) → n2-highmem-4」？

**說明：**
- `n2` 是機器系列
- `highmem` 是新的機器類型（記憶體更多，CPU 較少）
- `4` 是核心數量（保持不變）

可選擇相同或更高的核心數。

### Q5：我的集群沒有 Metrics Server，會怎樣？

**影響：**
- CPU 和記憶體使用率顯示為 0%
- 無法看到「CPU USE」和「MEM USE」欄位
- 決策建議仍會基於 Pod Request/Limit 進行

**解決方案：**

```bash
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
```

---

## 故障排除

### 故障 1：`kubectl: command not found`

**症狀：** 執行 python 腳本後立即報錯

**解決方案：**

```bash
# 安裝 kubectl
curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/darwin/amd64/kubectl"

# 或使用包管理工具
brew install kubectl  # macOS
apt-get install kubectl  # Ubuntu/Debian
```

### 故障 2：`json.JSONDecodeError`

**症狀：** 解析 JSON 失敗，無法繼續

**解決方案：**

```bash
# 檢查 kubectl 輸出格式
kubectl get nodes -o json | jq .

# 如果出錯，更新 kubectl
kubectl version --client
```

### 故障 3：無法連接到 API Server

**症狀：** 顯示「無法取得 Node 資訊」

**解決方案：**

```bash
# 檢查網路連線
ping <api-server-host>

# 檢查認證
kubectl auth can-i get nodes

# 檢查 kubeconfig 的有效性
kubectl config view
```

### 故障 4：權限不足

**症狀：** 「User cannot get nodes」

**解決方案：**

```bash
# 授予必要角色
gcloud projects add-iam-policy-binding <project-id> \
  --member=user:<email> \
  --role=roles/container.viewer

# 或更新 RBAC
kubectl create clusterrolebinding <name> \
  --clusterrole=view \
  --serviceaccount=default:default
```

### 故障 5：執行緩慢或超時

**症狀：** 工具執行超過 2 分鐘仍未完成

**解決方案：**

```python
# 增加超時時間
KUBECTL_TIMEOUT = 60  # 改為 60 秒

# 或減少 retry 次數
KUBECTL_RETRY_COUNT = 0
```

---

## 進階使用

### 自動化定期檢查

使用 Cron 定期執行檢查並將結果保存為日誌：

```bash
#!/bin/bash
# save_gke_report.sh

REPORT_DIR="/var/log/gke-reports"
mkdir -p $REPORT_DIR

TIMESTAMP=$(date +"%Y-%m-%d_%H-%M-%S")
OUTPUT_FILE="$REPORT_DIR/gke_report_${TIMESTAMP}.txt"

python3 /path/to/gke_resource_check.py > $OUTPUT_FILE 2>&1

# 設置 Cron job（每天上午 8 點）
# 0 8 * * * /path/to/save_gke_report.sh
```

### 集成到 Slack 通知

將報告發送到 Slack 頻道：

```bash
#!/bin/bash
# send_to_slack.sh

SLACK_WEBHOOK="https://hooks.slack.com/services/YOUR/WEBHOOK/URL"

MESSAGE=$(python3 /path/to/gke_resource_check.py)

curl -X POST -H 'Content-type: application/json' \
  --data "$(echo $MESSAGE | jq -Rs '{text: .}')" \
  $SLACK_WEBHOOK
```

### 與監測系統集成

將數據導出為 JSON 供其他系統使用：

```bash
# 修改腳本最後部分，添加 JSON 輸出
python3 -c "
import json
import subprocess

# ... 執行收集邏輯 ...

output = {
    'pools': [...],
    'nodes': [...],
    'risky_pods': [...]
}

with open('/tmp/gke_metrics.json', 'w') as f:
    json.dump(output, f)
"
```

---

## 支援與反饋

如有問題或建議，請：

1. 檢查 [故障排除](#故障排除) 部分
2. 檢查 kubectl 和 Metrics Server 的安裝狀態
3. 查看 Kubernetes 官方文檔：https://kubernetes.io/docs/
4. 查看 GKE 官方文檔：https://cloud.google.com/kubernetes-engine/docs

---

## 變更日誌

**版本 1.0**（初始版本）
- 支援 Pool 決策儀表板
- 支援節點詳細數據檢查
- 支援高風險 Pod 識別
- 支援記憶體和 CPU 使用率分析

---
