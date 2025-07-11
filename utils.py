"""Utility functions for PowerPointReviewer."""

import os
import re
import json
from pathlib import Path
from typing import Dict, List, Union, Optional, Any
from mutagen import File

from logger_utils import get_logger

logger = get_logger()

def sanitize_text_for_tts(text: str) -> str:
    """Sanitize text for TTS processing.
    
    Args:
        text: Input text to sanitize
        
    Returns:
        Sanitized text safe for TTS processing
    """
    if not text:
        return ""
    
    # Remove control characters
    sanitized = re.sub(r'[\x00-\x1f\x7f]+', '', text)
    
    # Remove excessive whitespace
    sanitized = re.sub(r'\s+', ' ', sanitized)
    
    # Strip leading/trailing whitespace
    sanitized = sanitized.strip()
    
    return sanitized

def validate_file_path(file_path: str, extensions: tuple) -> bool:
    """Validate if file path exists and has correct extension.
    
    Args:
        file_path: Path to validate
        extensions: Tuple of allowed extensions (e.g., ('.pptx', '.docx'))
        
    Returns:
        True if file is valid, False otherwise
    """
    if not file_path:
        return False
        
    path = Path(file_path)
    
    # Check if file exists
    if not path.exists():
        logger.error(f"File does not exist: {file_path}")
        return False
    
    # Check if it's a file (not directory)
    if not path.is_file():
        logger.error(f"Path is not a file: {file_path}")
        return False
    
    # Check extension
    if not any(file_path.lower().endswith(ext.lower()) for ext in extensions):
        logger.error(f"Invalid file extension for {file_path}. Expected: {extensions}")
        return False
    
    return True

def safe_file_operation(operation_func, *args, **kwargs) -> tuple[bool, Optional[Any], Optional[str]]:
    """Safely execute file operations with error handling.
    
    Args:
        operation_func: Function to execute
        *args: Arguments for the function
        **kwargs: Keyword arguments for the function
        
    Returns:
        Tuple of (success, result, error_message)
    """
    try:
        result = operation_func(*args, **kwargs)
        return True, result, None
    except FileNotFoundError as e:
        error_msg = f"File not found: {e}"
        logger.error(error_msg)
        return False, None, error_msg
    except PermissionError as e:
        error_msg = f"Permission denied: {e}"
        logger.error(error_msg)
        return False, None, error_msg
    except OSError as e:
        error_msg = f"OS error: {e}"
        logger.error(error_msg)
        return False, None, error_msg
    except Exception as e:
        error_msg = f"Unexpected error: {e}"
        logger.error(error_msg)
        return False, None, error_msg

def clean_audio_files(directory: str, extension: str = ".wav") -> tuple[bool, str]:
    """Clean audio files from directory.
    
    Args:
        directory: Directory to clean
        extension: File extension to clean (default: ".wav")
        
    Returns:
        Tuple of (success, message)
    """
    if not os.path.exists(directory):
        return False, f"Directory does not exist: {directory}"
    
    try:
        cleaned_count = 0
        for filename in os.listdir(directory):
            if filename.endswith(extension):
                file_path = os.path.join(directory, filename)
                os.remove(file_path)
                cleaned_count += 1
                logger.debug(f"Cleaned: {filename}")
        
        message = f"Cleaned {cleaned_count} files from {directory}"
        logger.info(message)
        return True, message
        
    except Exception as e:
        error_msg = f"Error cleaning directory {directory}: {e}"
        logger.error(error_msg)
        return False, error_msg

def get_audio_duration(file_path: str) -> float:
    """Get duration of audio file in seconds.
    
    Args:
        file_path: Path to audio file
        
    Returns:
        Duration in seconds, 0.0 if error
    """
    try:
        audio = File(file_path)
        if audio and hasattr(audio, 'info') and hasattr(audio.info, 'length'):
            return audio.info.length
    except Exception as e:
        logger.error(f"Error getting duration for {file_path}: {e}")
    
    return 0.0

def format_duration(seconds: float) -> str:
    """Format duration in seconds to human-readable string.
    
    Args:
        seconds: Duration in seconds
        
    Returns:
        Formatted duration string
    """
    if seconds < 60:
        return f'{round(seconds, 2)} 秒'
    
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f'{round(minutes)} 分钟 {round(secs)} 秒'
    
    hours, mins = divmod(minutes, 60)
    return f'{round(hours)} 小时 {round(mins)} 分钟 {round(secs)} 秒'

def safe_json_save(data: Dict, file_path: str) -> tuple[bool, Optional[str]]:
    """Safely save data to JSON file.
    
    Args:
        data: Data to save
        file_path: Path to save file
        
    Returns:
        Tuple of (success, error_message)
    """
    try:
        # Ensure directory exists
        Path(file_path).parent.mkdir(parents=True, exist_ok=True)
        
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        logger.info(f"Successfully saved JSON to {file_path}")
        return True, None
        
    except Exception as e:
        error_msg = f"Error saving JSON to {file_path}: {e}"
        logger.error(error_msg)
        return False, error_msg

def count_chinese_characters(text: str) -> int:
    """Count Chinese characters in text.
    
    Args:
        text: Text to analyze
        
    Returns:
        Number of Chinese characters
    """
    # Remove whitespace and count remaining characters
    cleaned_text = re.sub(r'\s+', '', text)
    return len(cleaned_text)

def generate_filename_with_suffix(base_path: str, suffix: str) -> str:
    """Generate filename with suffix while preserving extension.
    
    Args:
        base_path: Original file path
        suffix: Suffix to add
        
    Returns:
        New filename with suffix
    """
    path = Path(base_path)
    stem = path.stem
    extension = path.suffix
    return f"{stem}_{suffix}{extension}"