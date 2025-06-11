import requests
import pandas as pd
from sklearn.ensemble import IsolationForest
import time
import argparse
from datetime import datetime, timedelta
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

PROMETHEUS_URL = "http://prometheus.prometheus.svc.cluster.local:9090"
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
        logger.info(f"Querying Prometheus for {metric_name}: {query}")
        resp = requests.get(f"{PROMETHEUS_URL}/api/v1/query", params={'query': query}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data['status'] != 'success':
            logger.error(f"Prometheus query failed: {data.get('error', 'Unknown error')}")
            return []
        results = data['data']['result']
        logger.info(f"Got {len(results)} results for {metric_name}")
        return results
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to query Prometheus for {metric_name}: {str(e)}")
        return []
    except Exception as e:
        logger.error(f"Unexpected error querying Prometheus for {metric_name}: {str(e)}")
        return []

def get_pod_creation_time(pod_name, namespace):
    """Get the creation timestamp of a pod."""
    query = f'kube_pod_created{{pod="{pod_name}",namespace="{namespace}"}}'
    results = query_prometheus("pod_creation_time", query)
    if results:
        return float(results[0]['value'][1])
    return None

def build_feature_vectors():
    pod_data = {}
    current_time = time.time()
    
    # First get all running pods to filter out stale ones
    running_pods = set()
    running_results = query_prometheus("running_pods", 'kube_pod_status_phase{phase="Running"}')
    for entry in running_results:
        pod = entry['metric'].get('pod')
        ns = entry['metric'].get('namespace', 'default')
        if pod and float(entry['value'][1]) > 0:
            running_pods.add(f"{ns}/{pod}")
    
    logger.info(f"Found {len(running_pods)} running pods")
    
    for feature, promql in PROMQL_METRICS.items():
        results = query_prometheus(feature, promql)
        for entry in results:
            pod = entry['metric'].get('pod')
            ns = entry['metric'].get('namespace', 'default')
            val = float(entry['value'][1])
            timestamp = float(entry['value'][0])
            
            # Skip if pod is not in running state or metrics are too old (> 5 minutes)
            if not pod or f"{ns}/{pod}" not in running_pods or (current_time - timestamp) > 300:
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
    
    logger.info(f"Built feature vectors for {len(pod_data)} pods")
    return pod_data

def run_isolation_forest(pod_vectors, contamination=0.1):
    if not pod_vectors:
        logger.warning("No pod vectors to analyze")
        return pd.DataFrame()
        
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
    logger.info(f"Found {len(anomalies)} anomalies")
    return anomalies

def write_anomaly_pods_list(anomalies):
    """Write the list of anomalous pods to a file for Fluent-Bit to use."""
    try:
        with open("/fluent-bit/etc/shared/anomaly_pods.list", "w") as f:
            for _, row in anomalies.iterrows():
                pod_key = f"{row['namespace']}/{row['pod']}"
                f.write(f"{pod_key}\n")
        logger.info(f"Updated anomaly pods list with {len(anomalies)} entries")
    except Exception as e:
        logger.error(f"Failed to write anomaly pods list: {str(e)}")
    
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Anomaly Detector for Kubernetes Pods')
    parser.add_argument('--interval', type=int, default=60,
                      help='Detection interval in seconds (default: 60)')
    args = parser.parse_args()
    
    logger.info(f"Starting anomaly detector with {args.interval}s interval...")
    
    while True:
        try:
            logger.info("Starting new detection cycle...")
            feature_data = build_feature_vectors()

            if not feature_data:
                logger.warning("No metrics found, check Prometheus/KSM setup.")
                time.sleep(args.interval)
                continue

            anomalies = run_isolation_forest(feature_data)

            if anomalies.empty:
                logger.info("No anomalies detected.")
                # Clear the anomaly pods list if no anomalies are detected
                write_anomaly_pods_list(anomalies)
            else:
                logger.info("\nAnomalies Detected:")
                for _, row in anomalies.iterrows():
                    logger.info(f" Pod: {row['pod']}  | NS: {row['namespace']} | Score: {row['anomaly_score']:.3f}")
                    logger.info(" Feature vector:")
                    for f in PROMQL_METRICS:
                        key = "container_ready_flag" if f == "container_ready_flag_raw" else f
                        logger.info(f"  - {key:30s}: {row[key]}")
                    logger.info("-" * 50)
                
                # Update the anomaly pods list with the detected anomalies
                write_anomaly_pods_list(anomalies)
            
            logger.info(f"Waiting {args.interval} seconds until next detection cycle...")
            time.sleep(args.interval)
            
        except Exception as e:
            logger.error(f"An error occurred during detection cycle: {str(e)}")
            logger.info(f"Retrying in {args.interval} seconds...")
            time.sleep(args.interval)
