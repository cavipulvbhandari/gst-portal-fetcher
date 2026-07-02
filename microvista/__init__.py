"""Fetch GST notices and their PDFs from the Microvista Notice Alert portal.

Separate from :mod:`gstfetch` (which drives the *official* GST portal). This
package automates the third-party aggregator at ``noticealert.microvistatech.com``:
log in, walk each client GSTIN, open its Notices & Orders list, and download the
notice PDFs.
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
