"""
Home Assistant compatible logging utility.
Supports debug, info, and warning levels with proper formatting.
"""
import logging
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

_LOGGER = logging.getLogger(__name__)


class HACompatibleFormatter(logging.Formatter):
    """Formatter compatible with Home Assistant logging format."""
    
    def format(self, record: logging.LogRecord) -> str:
        """Format log record in HA-compatible JSON format."""
        log_entry = {
            "timestamp": datetime.fromtimestamp(record.created).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        return json.dumps(log_entry)


class LogCollector:
    """Collects logs in memory for testing purposes."""
    
    def __init__(self):
        self.logs = []
        self.handler = None
        
    def start_collecting(self, logger_name: str = "don_controller") -> None:
        """Start collecting logs from the specified logger."""
        logger = logging.getLogger(logger_name)
        self.handler = logging.StreamHandler()
        self.handler.setFormatter(HACompatibleFormatter())
        
        # Custom handler that appends to our logs list
        class CollectorHandler(logging.Handler):
            def __init__(self, collector):
                super().__init__()
                self.collector = collector
                
            def emit(self, record: logging.LogRecord):
                log_entry = {
                    "timestamp": datetime.fromtimestamp(record.created).isoformat(),
                    "level": record.levelname,
                    "logger": record.name,
                    "message": record.getMessage(),
                    "module": record.module,
                    "function": record.funcName,
                    "line": record.lineno,
                }
                self.collector.logs.append(log_entry)
        
        self.handler = CollectorHandler(self)
        logger.addHandler(self.handler)
        
    def stop_collecting(self, logger_name: str = "don_controller") -> None:
        """Stop collecting logs."""
        logger = logging.getLogger(logger_name)
        if self.handler:
            logger.removeHandler(self.handler)
            
    def get_logs(self) -> list[dict]:
        """Get all collected logs."""
        return self.logs
    
    def clear_logs(self) -> None:
        """Clear collected logs."""
        self.logs = []
    
    def save_to_file(self, filepath: str) -> None:
        """Save collected logs to a JSON file."""
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w') as f:
            json.dump(self.logs, f, indent=2)
    
    def filter_by_level(self, level: str) -> list[dict]:
        """Filter logs by level (DEBUG, INFO, WARNING, ERROR)."""
        return [log for log in self.logs if log["level"] == level]
    
    def filter_by_message(self, substring: str) -> list[dict]:
        """Filter logs containing a specific substring."""
        return [log for log in self.logs if substring.lower() in log["message"].lower()]


def setup_logging(logger_name: str = "don_controller", level: int = logging.INFO) -> logging.Logger:
    """Set up logging for the controller with HA-compatible format."""
    logger = logging.getLogger(logger_name)
    
    # Only configure if not already configured
    if not logger.handlers:
        logger.setLevel(level)
        
        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(HACompatibleFormatter())
        logger.addHandler(console_handler)
        
    return logger
