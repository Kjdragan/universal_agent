from logfire.query_client import LogfireQueryClient
from .config import get_settings
from typing import List, Dict, Any
import logging

logger = logging.getLogger(__name__)

class LogfireReader:
    def __init__(self):
        self.settings = get_settings()
        self.client = LogfireQueryClient(read_token=self.settings.logfire_read_token)

    def get_failures(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Fetches recent traces that have exceptions or error levels.
        """
        # Note: Logfire SQL dialect
        query = f"""
            SELECT 
                span_name,
                start_timestamp,
                trace_id,
                exception_type,
                exception_message,
                level,
                attributes
            FROM records 
            WHERE level >= 13 
               OR exception_type IS NOT NULL
            ORDER BY start_timestamp DESC 
            LIMIT {limit}
        """
        try:
            # Use query_json_rows to get a list of dicts directly
            result = self.client.query_json_rows(query)
            # Handle varied return types robustly
            if isinstance(result, dict) and 'rows' in result:
                 return result['rows']
            elif isinstance(result, list):
                return result
            elif result is None:
                return []
            return []
        except Exception as e:
            logger.error(f"Error querying Logfire failures: {e}")
            return []
