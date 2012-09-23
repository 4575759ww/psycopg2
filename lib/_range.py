"""Implementation of the Range type and adaptation

"""

# psycopg/_range.py - Implementation of the Range type and adaptation
#
# Copyright (C) 2012 Daniele Varrazzo  <daniele.varrazzo@gmail.com>
#
# psycopg2 is free software: you can redistribute it and/or modify it
# under the terms of the GNU Lesser General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# In addition, as a special exception, the copyright holders give
# permission to link this program with the OpenSSL library (or with
# modified versions of OpenSSL that use the same license as OpenSSL),
# and distribute linked combinations including the two.
#
# You must obey the GNU Lesser General Public License in all respects for
# all of the code used other than OpenSSL.
#
# psycopg2 is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
# FITNESS FOR A PARTICULAR PURPOSE.  See the GNU Lesser General Public
# License for more details.

import re

from psycopg2._psycopg import ProgrammingError, InterfaceError
from psycopg2.extensions import ISQLQuote, adapt, register_adapter
from psycopg2.extensions import new_type, new_array_type, register_type

class Range(object):
    """Python representation for a PostgreSQL range type.

    :param lower: lower bound for the range. None means unbound
    :param upper: upper bound for the range. None means unbound
    :param bounds: one of the literal strings ``()``, ``[)``, ``(]``, ``[]``,
        representing whether the lower or upper bounds are included
    :param empty: if true, the range is empty

    TODO: move this to the docs

    This Python type is only used to pass and retrieve range values to and
    from PostgreSQL and doesn't attempt to replicate the PostgreSQL range
    features: it doesn't perform normalization and doesn't implement all the
    operators supported.

    Although it is possible to instantiate `!Range` objects, the class doesn't
    have an adapter so you cannot normally pass these instances as query
    arguments. To use range objects as query arguments you can either use one
    of the provided subclasses, such as [TODO: the other] `IntRange` or create
    a custom one using `register_range()`.

    """
    __slots__ = ('_lower', '_upper', '_bounds')

    def __init__(self, lower=None, upper=None, bounds='[)', empty=False):
        if not empty:
            if bounds not in ('[)', '(]', '()', '[]'):
                raise ValueError("bound flags not valid: %r" % bounds)

            self._lower = lower
            self._upper = upper
            self._bounds = bounds
        else:
            self._lower = self._upper = self._bounds = None


    def __repr__(self):
        if self._bounds is None:
            return "%s(empty=True)" % self.__class__.__name__
        else:
            return "%s(%r, %r, %r)" % (self.__class__.__name__,
                self._lower, self._upper, self._bounds)

    @property
    def lower(self):
        """The lower bound of the range. None if empty or unbound."""
        return self._lower

    @property
    def upper(self):
        """The upper bound of the range. None if empty or unbound."""
        return self._upper

    @property
    def isempty(self):
        """True if the range is empty."""
        return self._bounds is None

    @property
    def lower_inf(self):
        """True if the range doesn't have a lower bound."""
        if self._bounds is None: return False
        return self._lower is None

    @property
    def upper_inf(self):
        """True if the range doesn't have an upper bound."""
        if self._bounds is None: return False
        return self._upper is None

    @property
    def lower_inc(self):
        """True if the lower bound is included in the range."""
        if self._bounds is None: return False
        if self._lower is None: return False
        return self._bounds[0] == '['

    @property
    def upper_inc(self):
        """True if the upper bound is included in the range."""
        if self._bounds is None: return False
        if self._upper is None: return False
        return self._bounds[1] == ']'

    def __contains__(self, x):
        if self._bounds is None: return False
        if self._lower is not None:
            if self._bounds[0] == '[':
                if x < self._lower: return False
            else:
                if x <= self._lower: return False

        if self._upper is not None:
            if self._bounds[1] == ']':
                if x > self._upper: return False
            else:
                if x >= self._upper: return False

        return True


def register_range(pgrange, pyrange, conn_or_curs, globally=False):
    """Register a typecaster and an adapter for range a range type.
    
    :param pgrange: the name of the PostgreSQL range type
    :param pyrange: a Range (strict) subclass, or just the name to give it
        (the class will be available as the `!range` attribute of the returned
        `RangeCaster`.
    :param conn_or_curs: a connection or cursor used to find the oid of the
        range and its subtype; the typecaster is registered in a scope limited
        to this object, unless *globally* is set to `!True`
    :param globally: if `!False` (default) register the typecaster only on
        *conn_or_curs*, otherwise register it globally
    :return: the registered `RangeCaster` instance responsible for the
        conversion
    """
    caster = RangeCaster._from_db(pgrange, pyrange, conn_or_curs)
    caster._register(not globally and conn_or_curs or None)
    return caster


class RangeAdapter(object):
    """`ISQLQuote` adapter for `Range` subclasses.

    This is an abstract class: concrete classes must set a `name` class
    attribute or override `getquoted()`.
    """
    name = None

    def __init__(self, adapted):
        self.adapted = adapted

    def __conform__(self, proto):
        if self._proto is ISQLQuote:
            return self

    def prepare(self, conn):
        self._conn = conn

    def getquoted(self):
        if self.name is None:
            raise NotImplementedError(
                'RangeAdapter must be subclassed overriding its name '
                'or the getquoted() method')

        r = self.adapted
        if r.isempty:
            return "'empty'::%s" % self.name

        if r.lower is not None:
            a = adapt(r.lower)
            if hasattr(a, 'prepare'):
                a.prepare(self._conn)
            lower = a.getquoted()
        else:
            lower = 'NULL'

        if r.upper is not None:
            a = adapt(r.upper)
            if hasattr(a, 'prepare'):
                a.prepare(self._conn)
            upper = a.getquoted()
        else:
            upper = 'NULL'

        return "%s(%s, %s, '%s')" % (
            self.name, lower, upper, r._bounds)


class RangeCaster(object):
    """Helper class to convert between `Range` and PostgreSQL range types."""
    def __init__(self, pgrange, pyrange, oid, subtype_oid, array_oid=None):
        self.subtype_oid = subtype_oid
        self._create_ranges(pgrange, pyrange)

        name = self.adapter.name or self.adapter.__class__.__name__

        self.typecaster = new_type((oid,), name, self.parse)

        if array_oid is not None:
            self.array_typecaster = new_array_type(
                (array_oid,), name + "ARRAY", self.typecaster)
        else:
            self.array_typecaster = None

    def _create_ranges(self, pgrange, pyrange):
        """Create Range and RangeAdapter classes if needed."""
        # if got a string create a new RangeAdapter concrete type (with a name)
        # else take it as an adapter.
        self.adapter = None
        if isinstance(pgrange, basestring):
            self.adapter = type(pgrange, (RangeAdapter,), {})
            self.adapter.name = pgrange
        else:
            try:
                if issubclass(pgrange, RangeAdapter) and pgrange is not RangeAdapter:
                    self.adapter = pgrange
            except TypeError:
                pass

        if self.adapter is None:
            raise TypeError(
                'pgrange must be a string or a RangeAdapter strict subclass')

        self.range = None
        try:
            if isinstance(pyrange, basestring):
                self.range = type(pyrange, (Range,), {})
            if issubclass(pyrange, Range) and pyrange is not Range:
                self.range = pyrange
        except TypeError:
            pass

        if self.range is None:
            raise TypeError(
                'pyrange must be a type or a Range strict subclass')

    @classmethod
    def _from_db(self, name, pyrange, conn_or_curs):
        """Return a `RangeCaster` instance for the type *pgrange*.

        Raise `ProgrammingError` if the type is not found.
        """
        from psycopg2.extensions import STATUS_IN_TRANSACTION
        from psycopg2.extras import _solve_conn_curs
        conn, curs = _solve_conn_curs(conn_or_curs)

        if conn.server_version < 90200:
            raise ProgrammingError("range types not available in version %s"
                % conn.server_version)

        # Store the transaction status of the connection to revert it after use
        conn_status = conn.status

        # Use the correct schema
        if '.' in name:
            schema, tname = name.split('.', 1)
        else:
            tname = name
            schema = None

        # get the type oid and attributes
        try:
            curs.execute("""\
select rngtypid, rngsubtype,
    (select typarray from pg_type where oid = rngtypid)
from pg_range r
join pg_type t on t.oid = rngtypid
join pg_namespace ns on ns.oid = typnamespace
where typname = %s and (%s is null or ns.nspname = %s);
""", (tname, schema, schema))

        except ProgrammingError:
            if not conn.autocommit:
                conn.rollback()
            raise
        else:
            rec = curs.fetchone()

            # revert the status of the connection as before the command
            if (conn_status != STATUS_IN_TRANSACTION
            and not conn.autocommit):
                conn.rollback()

        if not rec:
            raise ProgrammingError(
                "PostgreSQL type '%s' not found" % name)

        type, subtype, array = rec

        return RangeCaster(name, pyrange,
            oid=type, subtype_oid=subtype, array_oid=array)

    _re_range = re.compile(r"""
        ( \(|\[ )                   # lower bound flag
        (?:                         # lower bound:
          " ( (?: [^"] | "")* ) "   #   - a quoted string
          | ( [^",]+ )              #   - or an unquoted string
        )?                          #   - or empty (not catched)
        ,
        (?:                         # upper bound:
          " ( (?: [^"] | "")* ) "   #   - a quoted string
          | ( [^"\)\]]+ )           #   - or an unquoted string
        )?                          #   - or empty (not catched)
        ( \)|\] )                   # upper bound flag
        """, re.VERBOSE)

    _re_undouble = re.compile(r'(["\\])\1')

    def parse(self, s, cur=None):
        if s is None:
            return None

        if s == 'empty':
            return self.range(empty=True)

        m = self._re_range.match(s)
        if m is None:
            raise InterfaceError("failed to parse range: %s")

        lower = m.group(3)
        if lower is None:
            lower = m.group(2)
            if lower is not None:
                lower = self._re_undouble.sub(r"\1", lower)

        upper = m.group(5)
        if upper is None:
            upper = m.group(4)
            if upper is not None:
                upper = self._re_undouble.sub(r"\1", upper)

        if cur is not None:
            lower = cur.cast(self.subtype_oid, lower)
            upper = cur.cast(self.subtype_oid, upper)

        bounds = m.group(1) + m.group(6)

        return self.range(lower, upper, bounds)

    def _register(self, scope=None):
        register_type(self.typecaster, scope)
        if self.array_typecaster is not None:
            register_type(self.array_typecaster, scope)

        register_adapter(self.range, self.adapter)


class NumericRange(Range):
    """A `Range` suitable to pass Python numeric types to a PostgreSQL range."""
    pass

class DateRange(Range):
    """Represents :sql:`daterange` values."""
    pass

class DateTimeRange(Range):
    """Represents :sql:`tsrange` values."""
    pass

class DateTimeTZRange(Range):
    """Represents :sql:`tstzrange` values."""
    pass


# Special adaptation for NumericRange. Allows to pass number range regardless
# of whether they are ints, floats and what size of ints are, which are
# pointless in Python world. On the way back, no numeric range is casted to
# NumericRange, but only to their subclasses

class NumberRangeAdapter(RangeAdapter):
    """Adapt a range if the subtype doesn't need quotes."""
    def getquoted(self):
        r = self.adapted
        if r.isempty:
            return "'empty'"

        if not r.lower_inf:
            # not exactly: we are relying that none of these object is really
            # quoted (they are numbers). Also, I'm lazy and not preparing the
            # adapter because I assume encoding doesn't matter for these
            # objects.
            lower = adapt(r.lower).getquoted()
        else:
            lower = ''

        if not r.upper_inf:
            upper = adapt(r.upper).getquoted()
        else:
            upper = ''

        return "'%s%s,%s%s'" % (
            r._bounds[0], lower, upper, r._bounds[1])

# TODO: probably won't work with infs, nans and other tricky cases.
register_adapter(NumericRange, NumberRangeAdapter)


# Register globally typecasters and adapters for builtin range types.

# note: the adapter is registered more than once, but this is harmless.
int4range_caster = RangeCaster(NumberRangeAdapter, NumericRange,
    oid=3904, subtype_oid=23, array_oid=3905)
int4range_caster._register()

int8range_caster = RangeCaster(NumberRangeAdapter, NumericRange,
    oid=3926, subtype_oid=20, array_oid=3927)
int8range_caster._register()

numrange_caster = RangeCaster(NumberRangeAdapter, NumericRange,
    oid=3906, subtype_oid=1700, array_oid=3907)
numrange_caster._register()

daterange_caster = RangeCaster('daterange', DateRange,
    oid=3912, subtype_oid=1082, array_oid=3913)
daterange_caster._register()

tsrange_caster = RangeCaster('tsrange', DateTimeRange,
    oid=3908, subtype_oid=1114, array_oid=3909)
tsrange_caster._register()

tstzrange_caster = RangeCaster('tstzrange', DateTimeTZRange,
    oid=3910, subtype_oid=1184, array_oid=3911)
tstzrange_caster._register()


