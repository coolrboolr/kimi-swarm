"""Main application module with intentional issues for testing.

This file contains multiple issues that should be detected by agents:
1. Hardcoded API key (SecurityGuardian)
2. Duplicated code (RefactorArchitect)
3. Missing docstrings (StyleEnforcer)
4. O(n²) loop (PerformanceOptimizer)
5. Untested functions (TestEnhancer)
"""

import requests

# SECURITY ISSUE: Hardcoded API key
API_KEY = "sk-1234567890abcdef1234567890abcdef"
BASE_URL = "https://api.example.com"


def fetch_user_data(user_id):
    # STYLE ISSUE: Missing docstring
    # SECURITY ISSUE: Using hardcoded API key
    headers = {"Authorization": f"Bearer {API_KEY}"}
    response = requests.get(f"{BASE_URL}/users/{user_id}", headers=headers)
    return response.json()


def fetch_order_data(order_id):
    # REFACTOR ISSUE: Duplicated code (similar to fetch_user_data)
    headers = {"Authorization": f"Bearer {API_KEY}"}
    response = requests.get(f"{BASE_URL}/orders/{order_id}", headers=headers)
    return response.json()


def fetch_product_data(product_id):
    # REFACTOR ISSUE: More duplicated code
    headers = {"Authorization": f"Bearer {API_KEY}"}
    response = requests.get(f"{BASE_URL}/products/{product_id}", headers=headers)
    return response.json()


def find_matching_items(items, target_ids):
    # PERFORMANCE ISSUE: O(n²) complexity - checking if item.id in list
    # Should use a set for O(1) lookup
    result = []
    for item in items:
        if item["id"] in target_ids:  # O(n) lookup in list
            result.append(item)
    return result


def process_items(items):
    # TEST ISSUE: No unit tests for this function
    # STYLE ISSUE: Missing docstring
    processed = []
    for item in items:
        if item.get("active"):
            processed.append({
                "id": item["id"],
                "name": item["name"].upper(),
                "status": "PROCESSED"
            })
    return processed


def validate_email(email):
    # TEST ISSUE: Missing edge case tests
    # Simple validation, needs tests for edge cases
    if "@" in email and "." in email:
        return True
    return False


class DataProcessor:
    # STYLE ISSUE: Missing class docstring
    def __init__(self, config):
        self.config = config
        self.cache = {}

    def process(self, data):
        # STYLE ISSUE: Missing method docstring
        key = self._generate_cache_key(data)
        if key in self.cache:
            return self.cache[key]

        result = self._do_processing(data)
        self.cache[key] = result
        return result

    def _generate_cache_key(self, data):
        return str(hash(str(data)))

    def _do_processing(self, data):
        # Simulate some processing
        return {"processed": True, "data": data}


if __name__ == "__main__":
    print("Application started")
    # SECURITY ISSUE: Printing sensitive data
    print(f"Using API key: {API_KEY}")
