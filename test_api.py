import requests

# This is the exact same API endpoint and logic used in your app
API_BASE_URL = "https://alerts.kde.org"

def test_fetch():
    # Use a hardcoded area (e.g., Central Europe) to ensure we get *some* response
    params = {
        "min_lat": 40.0, 
        "max_lat": 55.0, 
        "min_lon": 0.0, 
        "max_lon": 20.0
    }
    
    print(f"--- Testing connection to {API_BASE_URL}/alert/area ---")
    try:
        response = requests.get(f"{API_BASE_URL}/alert/area", params=params, timeout=10)
        
        print(f"Status Code: {response.status_code}")
        print(f"Response Content: {response.text}")
        
        if response.status_code == 200:
            data = response.json()
            print(f"Successfully retrieved {len(data)} alert IDs.")
            
            if len(data) > 0:
                print(f"First Alert ID: {data[0]}")
                # Now test fetching one single alert
                print(f"--- Fetching single alert {data[0]} ---")
                single_response = requests.get(f"{API_BASE_URL}/alert/{data[0]}", timeout=10)
                print(f"Single Alert Status: {single_response.status_code}")
                print(f"Single Alert Content (first 200 chars): {single_response.text[:200]}")
        else:
            print("API did not return 200 OK.")
            
    except Exception as e:
        print(f"CRITICAL ERROR: {e}")

if __name__ == "__main__":
    test_fetch()