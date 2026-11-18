"""Suite-wide test setup.

The models declare ids with the PostgreSQL ``UUID`` type. Many tests create
those tables on in-memory SQLite, which has no such type: SQLAlchemy emits the
name ``UUID`` as is, and SQLite gives a column with that declared type NUMERIC
affinity. SQLAlchemy binds a UUID there as its 32-character hex string, so an
id whose hex happens to hold only decimal digits (or digits around one ``e``)
looks like a number and is stored as an integer or a real. Reading it back
hands ``uuid.UUID`` a float:

    AttributeError: 'float' object has no attribute 'replace'

About one ``uuid.uuid4()`` in a million has that shape, so the suite failed
at random, in whichever test drew the id.

Declare the column ``CHAR(32)`` on SQLite instead. That is what SQLAlchemy's
own portable ``Uuid`` type emits there. It has TEXT affinity, so every id is
stored as the string that was bound. PostgreSQL, which production uses, still
gets its native ``UUID`` type.
"""

from sqlalchemy.ext.compiler import compiles
from sqlalchemy.types import UUID


@compiles(UUID, "sqlite")
def _uuid_as_char_on_sqlite(element, compiler, **kw):
    return "CHAR(32)"
