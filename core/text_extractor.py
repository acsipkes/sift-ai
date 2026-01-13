# -*- coding: utf-8 -*-

"""
Text Extractor Module.

This module is responsible for converting various file formats (PDF, DOCX, HTML, TXT, etc.)
into raw text. The module follows a "soft dependency" principle: if an external library
(e.g., PyMuPDF) is missing, the application will not crash; only support for that
specific format will be disabled.
"""

import os
import logging
from typing import Optional, Dict, Any, List, Callable

# --- Import Optional Libraries ---
# Try-except blocks ensure that core functions (reading txt) work
# even if dependencies are not installed.

try:
    import fitz  # PyMuPDF
    _HAS_PDF = True
except ImportError:
    fitz = None
    _HAS_PDF = False

try:
    import docx
    _HAS_DOCX = True
except ImportError:
    docx = None
    _HAS_DOCX = False

try:
    from odf import text as odf_text, teletype
    from odf.opendocument import load as odf_load
    _HAS_ODT = True
except ImportError:
    odf_load = None
    _HAS_ODT = False

try:
    from bs4 import BeautifulSoup
    _HAS_BS4 = True
except ImportError:
    BeautifulSoup = None
    _HAS_BS4 = False

try:
    from striprtf.striprtf import rtf_to_text
    _HAS_RTF = True
except ImportError:
    rtf_to_text = None
    _HAS_RTF = False

# Log once if critical optional libraries are missing (DEBUG level usually, or warning)
if not all([_HAS_PDF, _HAS_DOCX, _HAS_ODT, _HAS_BS4, _HAS_RTF]):
    logging.warning("Some extractor libraries are missing. Certain file types will not be supported.")


# --- Constants ---
DEFAULT_HTML_PARSER = 'html.parser'
DEFAULT_EXCLUDED_TAGS = ["script", "style", "meta", "link", "header", "footer", "nav", "aside"]


# --- Helper: Safe file reading with encoding detection ---
def _read_text_file_safe(filepath: str, encodings: List[str] = None) -> Optional[str]:
    """Attempts to read a file using multiple encodings."""
    if encodings is None:
        encodings = ['utf-8', 'latin-1', 'cp1252', 'iso-8859-1']
    
    for enc in encodings:
        try:
            with open(filepath, 'r', encoding=enc) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
        except Exception as e:
            logging.error(f"File reading error ({filepath}): {e}")
            return None
    
    logging.error(f"Failed to detect file encoding: {filepath}")
    return None


# --- Specific Extractors ---

def _extract_plain_text(filepath: str) -> Optional[str]:
    """Reads simple text files (txt, py, md)."""
    return _read_text_file_safe(filepath)

def _extract_pdf(filepath: str) -> Optional[str]:
    """Processes PDF (PyMuPDF)."""
    try:
        doc = fitz.open(filepath)
        text = "".join([page.get_text("text") for page in doc])
        doc.close()
        return text
    except Exception as e:
        logging.error(f"PDF error ({filepath}): {e}")
        return None

def _extract_docx(filepath: str) -> Optional[str]:
    """Processes DOCX (python-docx)."""
    try:
        doc = docx.Document(filepath)
        return '\n'.join([p.text for p in doc.paragraphs])
    except Exception as e:
        logging.error(f"DOCX error ({filepath}): {e}")
        return None

def _extract_odt(filepath: str) -> Optional[str]:
    """Processes ODT (odfpy)."""
    try:
        textdoc = odf_load(filepath)
        all_paras = textdoc.getElementsByType(odf_text.P)
        return '\n'.join([teletype.extractText(p) for p in all_paras])
    except Exception as e:
        logging.error(f"ODT error ({filepath}): {e}")
        return None

def _extract_rtf(filepath: str) -> Optional[str]:
    """Processes RTF (striprtf)."""
    # RTF is often not UTF-8 but 8-bit encoded
    content = _read_text_file_safe(filepath)
    if content:
        try:
            return rtf_to_text(content, errors="ignore")
        except Exception as e:
            logging.error(f"RTF conversion error ({filepath}): {e}")
    return None

def _extract_html_content(content: str, options: Dict[str, Any] = None) -> Optional[str]:
    """Processes HTML string (BeautifulSoup logic)."""
    if not _HAS_BS4:
        logging.error("HTML processing not possible: beautifulsoup4 is missing.")
        return None

    opts = options or {}
    parser = opts.get('parser', DEFAULT_HTML_PARSER)
    exclude = opts.get('decompose_tags', DEFAULT_EXCLUDED_TAGS)
    separator = opts.get('text_separator', '\n')
    do_strip = opts.get('text_strip', True)

    try:
        soup = BeautifulSoup(content, parser)
        
        # Remove unnecessary elements
        if exclude:
            for tag in soup.find_all(exclude):
                tag.decompose()
        
        return soup.get_text(separator=separator, strip=do_strip)
    except Exception as e:
        logging.error(f"HTML parse error: {e}")
        return None

def _extract_html_file(filepath: str, options: Dict[str, Any] = None) -> Optional[str]:
    """Reads and processes an HTML file."""
    content = _read_text_file_safe(filepath)
    if content:
        return _extract_html_content(content, options)
    return None


# --- Dispatcher Logic ---

# Default extensions
_EXTRACTORS: Dict[str, Callable[[str], Optional[str]]] = {
    ext: _extract_plain_text 
    for ext in ['.txt', '.md', '.log', '.json', '.py', '.js', '.csv', '.xml', '.yml', '.yaml', '.ini', '.bat', '.sh', '.sql']
}

# Conditional registration
if _HAS_PDF:
    _EXTRACTORS['.pdf'] = _extract_pdf
if _HAS_DOCX:
    _EXTRACTORS['.docx'] = _extract_docx
if _HAS_ODT:
    _EXTRACTORS['.odt'] = _extract_odt
if _HAS_RTF:
    _EXTRACTORS['.rtf'] = _extract_rtf
if _HAS_BS4:
    _EXTRACTORS['.html'] = lambda fp, opts=None: _extract_html_file(fp, opts) # Wrapper for optional params
    _EXTRACTORS['.htm'] = _EXTRACTORS['.html']

# Public reference to query supported extensions (e.g., for GUI)
SUPPORTED_EXTRACTORS = _EXTRACTORS


# --- Public API ---

def extract_text_from_file(filepath: str, html_options: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """
    Extracts text based on the file extension.

    Args:
        filepath (str): Path to the file.
        html_options (Dict, optional): Extra settings for HTML files.

    Returns:
        str | None: The extracted text, or None on error.
    """
    if not os.path.isfile(filepath):
        logging.warning(f"File not found: {filepath}")
        return None

    _, ext = os.path.splitext(filepath)
    extractor = _EXTRACTORS.get(ext.lower())

    if not extractor:
        logging.info(f"Unsupported file type: {ext} ({os.path.basename(filepath)})")
        return None

    # Special handling for passing HTML options
    if ext.lower() in ['.html', '.htm'] and _HAS_BS4:
        # A lambda or direct call is registered here, but for safety:
        return _extract_html_file(filepath, html_options)
    
    return extractor(filepath)


def extract_text_from_html_content(html_content: str, html_options: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """
    Extracts text from raw HTML string.

    Args:
        html_content (str): The HTML code.
        html_options (Dict, optional): Parser settings.

    Returns:
        str | None: The cleaned text.
    """
    return _extract_html_content(html_content, html_options)