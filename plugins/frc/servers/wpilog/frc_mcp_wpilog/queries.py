"""Questions about an opened log: reduced views, values at a time, when a condition held, and text search.

Plain functions over a `LogLike`; `tools/` only validates parameters and shapes the reply. Times are ms since the aligned
origin (see `timeline.py`). The questions live in `windows.py`, `reduce_queries.py`, `diff_queries.py`, `when_queries.py` and
`text_queries.py`, and are re-exported here.
"""

from .diff_queries import diff
from .reduce_queries import compare, expand_topics, query, values_at
from .text_queries import search_text
from .when_queries import when
from .windows import FIND_NAMES_HINT, NOT_IN_LOG, QuerySpec, Range, aligned

__all__ = ["FIND_NAMES_HINT", "NOT_IN_LOG", "QuerySpec", "Range", "aligned", "compare", "diff", "expand_topics", "query",
           "search_text", "values_at", "when"]
