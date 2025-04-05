import requests
import pandas as pd
from sklearn.ensemble import IsolationForest

#PROMETHEUS_URL = "http://monitoring-kube-prometheus-prometheus.monitoring.svc.cluster.local:9090"
PROMETHEUS_URL = "http://localhost:9090"
# Define PromQL metrics as features
PROMQL_METRICS = {
    "restart_rate": 'increase(kube_pod_container_status_restarts_total[5m])',
    "CrashLoopBackOff_flag": 'kube_pod_container_status_waiting_reason{reason="CrashLoopBackOff"}',
    "ImagePullBackOff_flag": 'kube_pod_container_status_waiting_reason{reason="ImagePullBackOff"}',
    "OOMKilled_flag": 'kube_pod_container_status_terminated_reason{reason="OOMKilled"}',
    "pod_phase_pending_flag": 'kube_pod_status_phase{phase="Pending"}',
    "pod_failed_flag": 'kube_pod_status_phase{phase="Failed"}',
    "pod_running_flag": 'kube_pod_status_phase{phase="Running"}',
    "container_ready_flag_raw": 'kube_pod_container_status_ready{ready="false"}'
}

def query_prometheus(metric_name, query):
    try:
        resp = requests.get(f"{PROMETHEUS_URL}/api/v1/query", params={'query': query})
        resp.raise_for_status()
        return resp.json()['data']['result']
    except Exception as e:
        print(f"[ERROR] Failed to get {metric_name}: {str(e)}")
        return []

def build_feature_vectors():
    pod_data = {}
    for feature, promql in PROMQL_METRICS.items():
        results = query_prometheus(feature, promql)
        for entry in results:
            pod = entry['metric'].get('pod')
            ns = entry['metric'].get('namespace', 'default')
            val = float(entry['value'][1])
            if not pod:
                continue
            if pod not in pod_data:
                pod_data[pod] = {'namespace': ns}
            if feature == "container_ready_flag_raw":
                # convert ready="false" ⟶ container_ready_flag 0
                pod_data[pod]["container_ready_flag"] = 0 if val > 0 else 1
            else:
                pod_data[pod][feature] = val if feature == "restart_rate" else (1 if val > 0 else 0)
    # Fill missing features with 0
    expected = [
        "restart_rate", "CrashLoopBackOff_flag", "ImagePullBackOff_flag",
        "OOMKilled_flag", "pod_phase_pending_flag", "pod_failed_flag",
        "pod_running_flag", "container_ready_flag"
    ]
    for pod, data in pod_data.items():
        for f in expected:
            data.setdefault(f, 0)
    return pod_data

def run_isolation_forest(pod_vectors, contamination=0.1):
    df = pd.DataFrame.from_dict(pod_vectors, orient='index')
    
    # Separate out non-numerical/meta fields
    namespace = df.pop('namespace')
    original_features = df.columns.tolist()  # capture actual features

    model = IsolationForest(n_estimators=100, contamination=contamination, random_state=0)
    model.fit(df)

    # Score only using original features
    df['anomaly_score'] = model.decision_function(df[original_features])
    df['anomaly_flag'] = model.predict(df[original_features])  # -1 = anomaly
    df['pod'] = df.index
    df['namespace'] = namespace

    anomalies = df[df['anomaly_flag'] == -1]
    return anomalies
    
if __name__ == "__main__":
    print("\n Fetching Prometheus metrics...")
    feature_data = build_feature_vectors()

    if not feature_data:
        print("No metrics found, check Prometheus/KSM setup.")
        exit(1)

    print(f"Total pods processed: {len(feature_data)}")

    anomalies = run_isolation_forest(feature_data)

    if anomalies.empty:
        print("No anomalies detected.")
    else:
        print("\n Anomalies Detected:\n")
        for _, row in anomalies.iterrows():
            print(f" Pod: {row['pod']}  | NS: {row['namespace']} | Score: {row['anomaly_score']:.3f}")
            print(" Feature vector:")
            for f in PROMQL_METRICS:
                key = "container_ready_flag" if f == "container_ready_flag_raw" else f
                print(f"  - {key:30s}: {row[key]}")
            print("-" * 50)
