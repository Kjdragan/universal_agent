from logfire.query_client import LogfireQueryClient
from .config import get_settings
import pandas as pd
from typing import List, Dict, Any

class LogfireReader:
    def __init__(self):
        self.settings = get_settings()
        self.client = LogfireQueryClient(read_token=self.settings.logfire_read_token)

    def get_recent_traces(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Fetches the most recent traces from Logfire.
        Returns a list of dictionaries representing the trace records.
        """
        query = f"""
            SELECT 
                span_name,
                start_timestamp,
                end_timestamp,
                trace_id,
                attributes,
                level
            FROM records 
            ORDER BY start_timestamp DESC 
            LIMIT {limit}
        """
        
        # Logfire client returns a PyArrow table or pandas DataFrame usually
        # We'll assume it handles the query execution and conversion
        try:
            # Use query_json_rows to get a list of dicts directly
            result = self.client.query_json_rows(query)
            if result and 'rows' in result:
                return result['rows']
            return []
        except Exception as e:
            print(f"Error querying Logfire: {e}")
            return []

    def get_failures(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Fetches recent traces that have exceptions or error levels.
        """
        query = f"""
            SELECT 
                span_name,
                start_timestamp,
                trace_id,
                exception_type,
                exception_message,
                level
            FROM records 
            WHERE level >= 13  -- 13 is usually ERROR in some mappings, or check exception columns
               OR exception_type IS NOT NULL
            ORDER BY start_timestamp DESC 
            LIMIT {limit}
        """
        try:
            result = self.client.query_json_rows(query)
            if result and 'rows' in result:
                return result['rows']
            return []
        except Exception as e:
            print(f"Error querying Logfire failures: {e}")
            return []
