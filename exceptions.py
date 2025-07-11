"""Custom exceptions and error handling for PowerPointReviewer."""

from typing import Optional
from logger_utils import get_logger

logger = get_logger()

class PowerPointReviewerError(Exception):
    """Base exception class for PowerPointReviewer."""
    
    def __init__(self, message: str, details: Optional[str] = None):
        self.message = message
        self.details = details
        super().__init__(self.message)
    
    def __str__(self):
        if self.details:
            return f"{self.message}. Details: {self.details}"
        return self.message

class FileImportError(PowerPointReviewerError):
    """Exception raised when file import fails."""
    pass

class TTSError(PowerPointReviewerError):
    """Exception raised when TTS operations fail."""
    pass

class AudioProcessingError(PowerPointReviewerError):
    """Exception raised when audio processing fails."""
    pass

class ConfigurationError(PowerPointReviewerError):
    """Exception raised when configuration is invalid."""
    pass

class ValidationError(PowerPointReviewerError):
    """Exception raised when input validation fails."""
    pass

def handle_exception(exc: Exception, context: str = "") -> str:
    """Handle exceptions and return user-friendly error message.
    
    Args:
        exc: Exception to handle
        context: Additional context about where the error occurred
        
    Returns:
        User-friendly error message
    """
    context_prefix = f"{context}: " if context else ""
    
    if isinstance(exc, PowerPointReviewerError):
        error_msg = f"{context_prefix}{exc.message}"
        if exc.details:
            logger.error(f"{error_msg}. Details: {exc.details}")
        else:
            logger.error(error_msg)
        return exc.message
    
    elif isinstance(exc, FileNotFoundError):
        error_msg = f"{context_prefix}文件未找到"
        logger.error(f"{error_msg}: {exc}")
        return error_msg
    
    elif isinstance(exc, PermissionError):
        error_msg = f"{context_prefix}权限不足"
        logger.error(f"{error_msg}: {exc}")
        return error_msg
    
    elif isinstance(exc, OSError):
        error_msg = f"{context_prefix}文件系统错误"
        logger.error(f"{error_msg}: {exc}")
        return error_msg
    
    elif isinstance(exc, ValueError):
        error_msg = f"{context_prefix}数据格式错误"
        logger.error(f"{error_msg}: {exc}")
        return error_msg
    
    elif isinstance(exc, ImportError):
        error_msg = f"{context_prefix}模块导入错误"
        logger.error(f"{error_msg}: {exc}")
        return error_msg
    
    else:
        error_msg = f"{context_prefix}未知错误"
        logger.error(f"{error_msg}: {exc}")
        return f"{error_msg}: {str(exc)}"

def safe_execute(func, *args, context: str = "", **kwargs):
    """Safely execute a function with error handling.
    
    Args:
        func: Function to execute
        *args: Function arguments
        context: Context description for error logging
        **kwargs: Function keyword arguments
        
    Returns:
        Tuple of (success: bool, result: any, error_message: str)
    """
    try:
        result = func(*args, **kwargs)
        return True, result, ""
    except Exception as e:
        error_msg = handle_exception(e, context)
        return False, None, error_msg