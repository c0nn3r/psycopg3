.. index::
    pair: psycopg2; Differences

Differences from ``psycopg2``
=============================

`!psycopg3` uses the common DBAPI structure of many other database adapter and
tries to behave as close as possible to `!psycopg2`. There are however a few
differences to be aware of.


.. _server-side-binding:

Server-side binding
-------------------

`!psycopg3` sends the query and the parameters to the server separately,
instead of merging them client-side. PostgreSQL may behave slightly
differently in this case, usually throwing an error and suggesting to use an
explicit cast.

.. code:: python

    cur.execute("select '[10,20,30]'::jsonb -> 1").fetchone()
    # returns (20,)

    cur.execute("select '[10,20,30]'::jsonb -> %s", [1]).fetchone()
    # raises an exception:
    # UndefinedFunction: operator does not exist: jsonb -> numeric

    cur.execute("select '[10,20,30]'::jsonb -> %s::int", [1]).fetchone()
    # returns (20,)

PostgreSQL will also reject the execution of several queries at once
(separated by semicolon), if they contain parameters. If parameters are used
you should use distinct `execute()` calls; otherwise you may consider merging
the query client-side, using `psycopg3.sql` module.

Certain commands cannot be used with server-side binding, for instance
:sql:`SET` or :sql:`NOTIFY`::

    >>> cur.execute("SET timezone TO %s", ["utc"])
    ...
    psycopg3.errors.SyntaxError: syntax error at or near "$1"

Sometimes PostgreSQL offers an alternative (e.g. :sql:`SELECT set_config()`,
:sql:`SELECT pg_notify()`). If no alternative exist you can use `psycopg3.sql`
to compose the query client-side.

You cannot use :sql:`IN %s` and pass a tuple, because `IN ()` is an SQL
construct. You must use :sql:`= any(%s)` and pass a list. Note that this also
works for an empty list, whereas an empty tuple would have resulted in an
error.


.. _diff-adapt:

Different adaptation system
---------------------------

The adaptation system has been completely rewritten, in order to address
server-side parameters adaptation, but also to consider performance,
flexibility, ease of customization.

Builtin data types should work as expected; if you have wrapped a custom data
type you should check the :ref:`adaptation` topic.


.. _diff-copy:

Copy is no more file-based
--------------------------

`!psycopg2` exposes :ref:`a few copy methods <pg2:copy>` to interact with
PostgreSQL :sql:`COPY`. The interface doesn't make easy to load
dynamically-generated data to the database.

There is now a single `~psycopg3.Cursor.copy()` method, which is similar to
`!psycopg2` `!copy_expert()` in accepting a free-form :sql:`COPY` command and
returns an object to read/write data, block-wise or record-wise. The different
usage pattern also enables :sql:`COPY` to be used in async interactions.

See :ref:`copy` for the details.


.. _diff-with:

``with`` connection
-------------------

When the connection is used as context manager, at the end of the context
the connection will be closed. In `!psycopg2` only the transaction is closed,
so a connection can be used in several contexts, but the behaviour is
surprising for people used to several other Python classes wrapping
resources, such as files.


.. _diff-callproc:

``callproc()`` is gone
----------------------

`cursor.callproc()` is not implemented. The method has a simplistic
semantic which doesn't account for PostgreSQL positional parameters,
procedures, set-returning functions. Use a normal
`~psycopg3.Cursor.execute()` with :sql:`SELECT function_name(...)` or
:sql:`CALL procedure_name(...)` instead.


What's new in psycopg3
----------------------

.. admonition:: TODO

    to be completed

- `asyncio` support.
- Several data types are adapted out-of-the-box: uuid, network, range, bytea,
  array of any supported type are dealt with automatically.
- Access to the low-level libpq functions.
