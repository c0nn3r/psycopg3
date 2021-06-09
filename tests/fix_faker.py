import datetime as dt
import importlib
import ipaddress
from math import isnan
from uuid import UUID
from random import choice, random, randrange
from decimal import Decimal
from collections import deque

import pytest

import psycopg3
from psycopg3 import sql
from psycopg3.adapt import Format
from psycopg3.types.range import Range
from psycopg3.wrappers.numeric import Int4, Int8


@pytest.fixture
def faker(conn):
    return Faker(conn)


class Faker:
    """
    An object to generate random records.
    """

    json_max_level = 3
    json_max_length = 10
    str_max_length = 100
    list_max_length = 20
    tuple_max_length = 15

    def __init__(self, connection):
        self.conn = connection
        self._format = Format.BINARY
        self.records = []

        self._schema = None
        self._types = None
        self._types_names = None
        self._makers = {}
        self.table_name = sql.Identifier("fake_table")

    @property
    def format(self):
        return self._format

    @format.setter
    def format(self, format):
        if format != Format.BINARY:
            pytest.xfail("faker to extend to all text dumpers")
        self._format = format

    @property
    def schema(self):
        if not self._schema:
            self._schema = self.choose_schema()
        return self._schema

    @schema.setter
    def schema(self, schema):
        self._schema = schema
        self._types_names = None

    @property
    def fields_names(self):
        return [sql.Identifier(f"fld_{i}") for i in range(len(self.schema))]

    @property
    def types(self):
        if not self._types:
            self._types = sorted(
                self.get_supported_types(), key=lambda cls: cls.__name__
            )
        return self._types

    @property
    def types_names(self):
        if self._types_names:
            return self._types_names

        record = self.make_record(nulls=0)
        tx = psycopg3.adapt.Transformer(self.conn)
        types = []
        registry = self.conn.adapters.types
        for value in record:
            dumper = tx.get_dumper(value, self.format)
            dumper.dump(value)  # load the oid if it's dynamic (e.g. array)
            info = registry.get(dumper.oid) or registry.get("text")
            if dumper.oid == info.array_oid:
                types.append(sql.SQL("{}[]").format(sql.Identifier(info.name)))
            else:
                types.append(sql.Identifier(info.name))

        self._types_names = types
        return types

    @property
    def drop_stmt(self):
        return sql.SQL("drop table if exists {}").format(self.table_name)

    @property
    def create_stmt(self):
        fields = []
        for name, type in zip(self.fields_names, self.types_names):
            fields.append(sql.SQL("{} {}").format(name, type))

        fields = sql.SQL(", ").join(fields)
        return sql.SQL(
            "create table {table} (id serial primary key, {fields})"
        ).format(table=self.table_name, fields=fields)

    @property
    def insert_stmt(self):
        phs = [
            sql.Placeholder(format=self.format)
            for i in range(len(self.schema))
        ]
        return sql.SQL("insert into {} ({}) values ({})").format(
            self.table_name,
            sql.SQL(", ").join(self.fields_names),
            sql.SQL(", ").join(phs),
        )

    @property
    def select_stmt(self):
        fields = sql.SQL(", ").join(self.fields_names)
        return sql.SQL("select {} from {} order by id").format(
            fields, self.table_name
        )

    def choose_schema(self, ncols=20):
        schema = [self.make_schema(choice(self.types)) for i in range(ncols)]
        return schema

    def make_records(self, nrecords):
        self.records = [self.make_record(nulls=0.05) for i in range(nrecords)]

    def make_record(self, nulls=0):
        if not nulls:
            return tuple(self.make(spec) for spec in self.schema)
        else:
            return tuple(
                self.make(spec) if random() > nulls else None
                for spec in self.schema
            )

    def assert_record(self, got, want):
        for spec, g, w in zip(self.schema, got, want):
            if g is None and w is None:
                continue
            m = self.get_matcher(spec)
            m(spec, g, w)

    def get_supported_types(self):
        dumpers = self.conn.adapters._dumpers[self.format]
        rv = set()
        for cls in dumpers.keys():
            if isinstance(cls, str):
                cls = deep_import(cls)
            rv.add(cls)

        # check all the types are handled
        for cls in rv:
            self.get_maker(cls)

        return rv

    def make_schema(self, cls):
        """Create a schema spec from a Python type.

        A schema specifies what Postgres type to generate when a Python type
        maps to more than one (e.g. tuple -> composite, list -> array[],
        datetime -> timestamp[tz]).

        A schema for a type is represented by a tuple (type, ...) which the
        matching make_*() method can interpret, or just type if the type
        doesn't require further specification.
        """
        meth = self._get_method("schema", cls)
        return meth(cls) if meth else cls

    def get_maker(self, spec):
        cls = spec if isinstance(spec, type) else spec[0]

        try:
            return self._makers[cls]
        except KeyError:
            pass

        meth = self._get_method("make", cls)
        if meth:
            self._makers[cls] = meth
            return meth
        else:
            raise NotImplementedError(
                f"cannot make fake objects of class {cls}"
            )

    def get_matcher(self, spec):
        cls = spec if isinstance(spec, type) else spec[0]
        meth = self._get_method("match", cls)
        return meth if meth else self.match_any

    def _get_method(self, prefix, cls):
        name = cls.__name__
        if cls.__module__ != "builtins":
            name = f"{cls.__module__}.{name}"

        parts = name.split(".")
        for i in range(len(parts)):
            mname = f"{prefix}_{'_'.join(parts[-(i + 1) :])}"
            meth = getattr(self, mname, None)
            if meth:
                return meth

        return None

    def make(self, spec):
        # spec can be a type or a tuple (type, options)
        return self.get_maker(spec)(spec)

    def match_any(self, spec, got, want):
        assert got == want

    # methods to implement specific objects

    def make_bool(self, spec):
        return choice((True, False))

    def make_bytearray(self, spec):
        return self.make_bytes(spec)

    def make_bytes(self, spec):
        length = randrange(self.str_max_length)
        return spec(bytes([randrange(256) for i in range(length)]))

    def make_date(self, spec):
        day = randrange(dt.date.max.toordinal())
        return dt.date.fromordinal(day + 1)

    def schema_datetime(self, cls):
        return self.schema_time(cls)

    def make_datetime(self, spec):
        delta = dt.datetime.max - dt.datetime.min
        micros = randrange((delta.days + 1) * 24 * 60 * 60 * 1_000_000)
        rv = dt.datetime.min + dt.timedelta(microseconds=micros)
        if spec[1]:
            rv = rv.replace(tzinfo=self._make_tz(spec))
        return rv

    def make_Decimal(self, spec):
        if random() >= 0.99:
            if self.conn.info.server_version >= 140000:
                return Decimal(choice(["NaN", "sNaN", "Inf", "-Inf"]))
            else:
                return Decimal(choice(["NaN", "sNaN"]))

        sign = choice("+-")
        num = choice(["0.zd", "d", "d.d"])
        while "z" in num:
            ndigits = randrange(1, 20)
            num = num.replace("z", "0" * ndigits, 1)
        while "d" in num:
            ndigits = randrange(1, 20)
            num = num.replace(
                "d", "".join([str(randrange(10)) for i in range(ndigits)]), 1
            )
        expsign = choice(["e+", "e-", ""])
        exp = randrange(20) if expsign else ""
        rv = Decimal(f"{sign}{num}{expsign}{exp}")
        return rv

    def match_Decimal(self, spec, got, want):
        if got is not None and got.is_nan():
            assert want.is_nan()
        else:
            assert got == want

    def make_float(self, spec):
        if random() <= 0.99:
            # this exponent should generate no inf
            return float(
                f"{choice('-+')}0.{randrange(1 << 53)}e{randrange(-310,309)}"
            )
        else:
            return choice(
                (0.0, -0.0, float("-inf"), float("inf"), float("nan"))
            )

    def match_float(self, spec, got, want):
        if got is not None and isnan(got):
            assert isnan(want)
        else:
            assert got == want

    def make_int(self, spec):
        return randrange(-(1 << 90), 1 << 90)

    def make_Int2(self, spec):
        return spec(randrange(-(1 << 15), 1 << 15))

    def make_Int4(self, spec):
        return spec(randrange(-(1 << 31), 1 << 31))

    def make_Int8(self, spec):
        return spec(randrange(-(1 << 63), 1 << 63))

    def make_IPv4Address(self, spec):
        return ipaddress.IPv4Address(bytes(randrange(256) for _ in range(4)))

    def make_IPv4Interface(self, spec):
        prefix = randrange(32)
        return ipaddress.IPv4Interface(
            (bytes(randrange(256) for _ in range(4)), prefix)
        )

    def make_IPv4Network(self, spec):
        return self.make_IPv4Interface(spec).network

    def make_IPv6Address(self, spec):
        return ipaddress.IPv6Address(bytes(randrange(256) for _ in range(16)))

    def make_IPv6Interface(self, spec):
        prefix = randrange(128)
        return ipaddress.IPv6Interface(
            (bytes(randrange(256) for _ in range(16)), prefix)
        )

    def make_IPv6Network(self, spec):
        return self.make_IPv6Interface(spec).network

    def make_Json(self, spec):
        return spec(self._make_json())

    def match_Json(self, spec, got, want):
        if want is not None:
            want = want.obj
        assert got == want

    def make_Jsonb(self, spec):
        return spec(self._make_json())

    def match_Jsonb(self, spec, got, want):
        return self.match_Json(spec, got, want)

    def make_JsonFloat(self, spec):
        # A float limited to what json accepts
        # this exponent should generate no inf
        return float(
            f"{choice('-+')}0.{randrange(1 << 20)}e{randrange(-15,15)}"
        )

    def schema_list(self, cls):
        while 1:
            scls = choice(self.types)
            if scls is not cls:
                break

        return (cls, self.make_schema(scls))

    def make_list(self, spec):
        # don't make empty lists because they regularly fail cast
        length = randrange(1, self.list_max_length)
        spec = spec[1]
        return [self.make(spec) for i in range(length)]

    def match_list(self, spec, got, want):
        assert len(got) == len(want)
        m = self.get_matcher(spec[1])
        for g, w in zip(got, want):
            m(spec, g, w)

    def make_memoryview(self, spec):
        return self.make_bytes(spec)

    def make_NoneType(self, spec):
        return None

    def make_Oid(self, spec):
        return spec(randrange(1 << 32))

    def schema_Range(self, cls):
        subtypes = [
            Int4,
            Int8,
            Decimal,
            dt.date,
            (dt.datetime, True),
            (dt.datetime, False),
        ]
        return (cls, choice(subtypes))

    def make_Range(self, spec):
        if random() < 0.02:
            return Range(empty=True)

        bounds = []
        while len(bounds) < 2:
            if random() < 0.05:
                bounds.append(None)
                continue

            val = self.make(spec[1])
            # NaN are allowed in a range, but comparison in Python get tricky.
            if spec[1] is Decimal and val.is_nan():
                continue

            bounds.append(val)

        if bounds[0] is not None and bounds[1] is not None:
            if bounds[0] > bounds[1]:
                bounds.reverse()

        return Range(bounds[0], bounds[1], choice("[(") + choice("])"))

    def match_Range(self, spec, got, want):
        # normalise the bounds of unbounded ranges
        if want.lower is None and want.lower_inc:
            want = type(want)(want.lower, want.upper, "(" + want.bounds[1])
        if want.upper is None and want.upper_inc:
            want = type(want)(want.lower, want.upper, want.bounds[0] + ")")
        return got == want

    def make_str(self, spec, length=0):
        if not length:
            length = randrange(self.str_max_length)

        rv = []
        while len(rv) < length:
            c = randrange(1, 128) if random() < 0.5 else randrange(1, 0x110000)
            if not (0xD800 <= c <= 0xDBFF or 0xDC00 <= c <= 0xDFFF):
                rv.append(c)

        return "".join(map(chr, rv))

    def schema_time(self, cls):
        # Choose timezone yes/no
        return (cls, choice([True, False]))

    def make_time(self, spec):
        val = randrange(24 * 60 * 60 * 1_000_000)
        val, ms = divmod(val, 1_000_000)
        val, s = divmod(val, 60)
        h, m = divmod(val, 60)
        tz = self._make_tz(spec) if spec[1] else None
        return dt.time(h, m, s, ms, tz)

    def make_timedelta(self, spec):
        return choice([dt.timedelta.min, dt.timedelta.max]) * random()

    def schema_tuple(self, cls):
        length = randrange(1, self.tuple_max_length)
        return (cls, self.choose_schema(ncols=length))

    def make_UUID(self, spec):
        return UUID(bytes=bytes([randrange(256) for i in range(16)]))

    def _make_json(self, container_chance=0.66):
        rec_types = [list, dict]
        scal_types = [type(None), int, JsonFloat, bool, str]
        if random() < container_chance:
            cls = choice(rec_types)
            if cls is list:
                return [
                    self._make_json(container_chance=container_chance / 2.0)
                    for i in range(randrange(self.json_max_length))
                ]
            elif cls is dict:
                return {
                    self.make_str(str, 15): self._make_json(
                        container_chance=container_chance / 2.0
                    )
                    for i in range(randrange(self.json_max_length))
                }
            else:
                assert False, f"unknown rec type: {cls}"

        else:
            cls = choice(scal_types)
            return self.make(cls)

    def _make_tz(self, spec):
        minutes = randrange(-12 * 60, 12 * 60 + 1)
        return dt.timezone(dt.timedelta(minutes=minutes))


class JsonFloat:
    pass


def deep_import(name):
    parts = deque(name.split("."))
    seen = []
    if not parts:
        raise ValueError("name must be a dot-separated name")

    seen.append(parts.popleft())
    thing = importlib.import_module(seen[-1])
    while parts:
        attr = parts.popleft()
        seen.append(attr)

        if hasattr(thing, attr):
            thing = getattr(thing, attr)
        else:
            thing = importlib.import_module(".".join(seen))

    return thing
