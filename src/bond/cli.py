"""The bond command.

Workspaces live in a Bond home (bond.local): `bond new` makes one, `bond add` adds files to it as sources,
`bond transcribe` writes the text of a source with none of its own (a PDF) as a document citing where it was read,
`bond link` lets documents be imported from one into another, and `bond grant` lets an agent's server
configuration work in one. Those are a person's decisions; an agent gets none of these commands, only the
authoring tools `bond mcp` serves.

    bond new smith                                       # a workspace in the Bond home
    bond add smith fax.md notes.md                       # files, as sources, named after the files
    bond add smith fax-v2.md --as fax.md --update        # a new version of a document
    bond transcribe smith report.pdf                     # its text, by a producer Bond runs: report.pdf.pypdf
    bond transcribe smith fax.pdf --with reducto --output fax.reducto.json   # a parse Reducto returned
    bond link smith --from criteria                      # documents in criteria may be imported into smith
    bond grant smith --to desktop                        # bond mcp --grants desktop may work in smith
    bond workspaces                                      # what the home holds, its links and grants

`bond view` serves workspaces to the viewer in a browser, read-only, on localhost. `bond mcp` serves them to an
agent over MCP (stdio): the authoring tools, for the workspaces named and the ones they link to.

    bond view                                                               # every workspace in the home
    bond view --workspace smith --workspace ./cases/001                     # a name in the home, a directory
    bond view --postgres postgresql://… --name case-001 --s3-bucket evidence  # one in Postgres, blobs in S3
    bond mcp --workspace smith --workspace jones                            # an agent's workspaces
    bond mcp --workspace ./cases/001 --import-from ./policies               # a directory, and what it may import
    bond mcp --grants desktop                                               # what a person granted desktop

A workspace given with a / in it is a directory; anything else is a name in the Bond home (BOND_HOME, or
--home). Opening a workspace to view never creates one; `bond mcp` creates a new local one in an existing
directory, but a name must already be in the home. Postgres needs psycopg 3 and psycopg_pool, S3 needs boto3:
installed by you, configured as they usually are (credentials, region and endpoint from the environment, or
--s3-endpoint). With --postgres, --name and --import-from name workspaces in the same database, with their blobs
in the same place.

`bond import` brings a document from one workspace into another, with its provenance:

    bond import --from policies --into ./cases/001 policy/IP-112
"""

from __future__ import annotations

import argparse
import importlib
import ipaddress
import os
import sys
import threading
import webbrowser
from collections.abc import Generator, Sequence
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

from .api import Bond
from .formats import spec
from .http.viewer import GRAPH_LIMIT
from .local import Home, is_path
from .models import BondError
from .sources import NewSource, add_sources, readable
from .storage import Blobs
from .workspaces import Fixed, Workspaces

if TYPE_CHECKING:
    from .mcp.server import Served


def main(argv: Sequence[str] | None = None) -> int:
    args = parse(argv)
    commands = {"view": run_view, "mcp": run_mcp, "import": run_import, "new": run_new, "add": run_add,
                "transcribe": run_transcribe, "link": run_link, "grant": run_grant, "workspaces": run_workspaces}
    return commands[args.command](args)


def parse(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """The command line's arguments; exits with a usage message if they don't go together."""
    parser = argparse.ArgumentParser(prog="bond", description="Bond, the filesystem that remembers why.")
    commands = parser.add_subparsers(dest="command", required=True)
    view = commands.add_parser("view", help="browse workspaces in the viewer", description=__doc__,
                               formatter_class=argparse.RawDescriptionHelpFormatter)
    _store_arguments(view, required=False, names=True)
    view.add_argument("--host", default="127.0.0.1", help="address to listen on (default: 127.0.0.1)")
    view.add_argument("--port", type=int, default=8000)
    view.add_argument("--graph-limit", type=int, default=GRAPH_LIMIT,
                      help=f"draw workspaces of up to this many versions whole (default: {GRAPH_LIMIT}); browse "
                           "larger ones")
    view.add_argument("--no-browser", action="store_true", help="don't open a browser")
    mcp = commands.add_parser("mcp", help="serve workspaces' authoring tools to an agent over MCP (stdio)",
                              description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    _store_arguments(mcp, required=True, names=False).add_argument(
        "--grants", metavar="NAME", help="serve the workspaces a person granted this configuration (in the view, or "
                                         "with bond grant), from the Bond home")
    mcp.add_argument("--import-from", action="append", default=[], metavar="WORKSPACE",
                     help="a workspace the agent may import documents from: a directory, or with --postgres a "
                          "name (repeatable)")
    mcp.add_argument("--import-depth", type=_depth, default=0, help="dependency hops to copy, or 'all' (default: 0)")
    mcp.add_argument("--import-limit", type=_positive, default=100, help="maximum versions visited per import")
    bring = commands.add_parser("import", help="bring a document from one local workspace into another")
    bring.add_argument("document", help="the document's name in the workspace it comes from")
    bring.add_argument("--from", dest="source", required=True, help="the workspace it comes from: a name or directory")
    bring.add_argument("--into", required=True,
                       help="the workspace it goes into: a name in the home, or a directory (created if new)")
    bring.add_argument("--as", dest="name", help="its name there (default: the same)")
    bring.add_argument("--depth", type=_depth, default=0, help="dependency hops to copy, or 'all' (default: 0)")
    bring.add_argument("--limit", type=_positive, default=100, help="maximum versions to visit (default: 100)")
    _home_argument(bring)
    new = commands.add_parser("new", help="create a workspace in the Bond home")
    new.add_argument("workspace", help="its name")
    _home_argument(new)
    add = commands.add_parser("add", help="add files to a workspace as sources")
    add.add_argument("workspace", help="a name in the Bond home, or a directory")
    add.add_argument("files", nargs="+", type=Path, metavar="FILE")
    add.add_argument("--as", dest="name", help="the document's name (default: the file's name); one file only")
    add.add_argument("--update", action="store_true",
                     help="add a file whose name is taken as that document's next version")
    _home_argument(add)
    transcribe = commands.add_parser("transcribe", help="write the text of a source with none of its own (a PDF), "
                                                        "so it can be cited")
    transcribe.add_argument("workspace", help="a name in the Bond home, or a directory")
    transcribe.add_argument("document", help="the source's document")
    transcribe.add_argument("--with", dest="producer", metavar="PRODUCER",
                            help="the producer, e.g. pypdf or reducto (default: one Bond runs that reads the "
                                 "source's format: pypdf, for a PDF's own text layer)")
    transcribe.add_argument("--output", type=Path, metavar="FILE",
                            help="record this file as the producer's output instead of running it")
    _home_argument(transcribe)
    link = commands.add_parser("link", help="let documents be imported from one workspace into another")
    link.add_argument("workspace", help="the workspace documents may be imported into")
    link.add_argument("--from", dest="source", required=True, help="the workspace they may come from")
    link.add_argument("--remove", action="store_true", help="remove the link instead; what was imported stays")
    _home_argument(link)
    grant = commands.add_parser("grant", help="let an MCP server configuration's agent work in a workspace")
    grant.add_argument("workspace", help="a workspace in the Bond home")
    grant.add_argument("--to", dest="config", required=True, help="the configuration, as given to bond mcp --grants")
    grant.add_argument("--remove", action="store_true", help="revoke the grant instead; what it wrote stays")
    _home_argument(grant)
    listing = commands.add_parser("workspaces", help="list the workspaces in the Bond home, their links and grants")
    _home_argument(listing)
    args = parser.parse_args(argv)
    if args.command == "mcp" and args.grants and args.import_from:
        parser.error("--import-from goes with --workspace; in grant mode, workspaces import from what they link to")
    if args.command in ("view", "mcp"):
        if args.postgres and (args.blobs is None) == (args.s3_bucket is None):
            parser.error("--postgres needs exactly one of --blobs and --s3-bucket")
        if not args.postgres and (args.blobs or args.s3_bucket or args.name):
            parser.error("--name, --blobs and --s3-bucket go with --postgres; a local workspace holds its own blobs")
    return args


def _depth(text: str) -> int | None:
    """A number of dependency hops, or 'all' (None) for no bound."""
    if text == "all":
        return None
    if not text.isdigit():
        raise argparse.ArgumentTypeError(f"a depth is a number of hops, or 'all'; got {text!r}")
    return int(text)


def _positive(text: str) -> int:
    if not text.isdigit() or int(text) < 1:
        raise argparse.ArgumentTypeError(f"must be a whole number, at least 1; got {text!r}")
    return int(text)


def _store_arguments(command: argparse.ArgumentParser, required: bool, names: bool,
                     ) -> argparse._MutuallyExclusiveGroup:
    where = command.add_mutually_exclusive_group(required=required)
    where.add_argument("--workspace", action="append",
                       help="a workspace: its name in the Bond home, or a directory (a value with a / in it) "
                            "(repeatable)")
    where.add_argument("--postgres", metavar="DSN", help="a Postgres store, e.g. postgresql://user@host/db")
    command.add_argument("--name", action="append" if names else "store",
                         help="with --postgres: a workspace's name (needed if it holds several)"
                              + (" (repeatable)" if names else ""))
    command.add_argument("--blobs", type=Path, help="with --postgres: a local blob directory")
    command.add_argument("--s3-bucket", help="with --postgres: the S3 bucket holding the blobs")
    command.add_argument("--s3-prefix", default="", help="the key prefix of the blobs in the bucket")
    command.add_argument("--s3-endpoint", help="an S3-compatible endpoint URL (MinIO, R2, …)")
    _home_argument(command)
    return where


def _home_argument(command: argparse.ArgumentParser) -> None:
    command.add_argument("--home", type=Path, help="the Bond home (default: BOND_HOME, or the platform's app data)")


def home_of(args: argparse.Namespace) -> Home:
    return Home(args.home) if args.home else Home.default()


# --- a person's decisions ----------------------------------------------------------


def run_new(args: argparse.Namespace) -> int:
    """bond new: a workspace in the home, named as given."""
    try:
        path = home_of(args).create(args.workspace)
        print(f"created workspace {args.workspace!r} in {path}")
        return 0
    except BondError as e:
        print(f"bond new: {e}", file=sys.stderr)
        return 1


def run_add(args: argparse.Namespace) -> int:
    """bond add: files into a workspace as sources (bond.sources), each a copy that stays as it was added,
    with where it came from. Every file is checked before any is added."""
    try:
        if args.name and len(args.files) > 1:
            raise BondError("--as names one file; add the others separately")
        for f in args.files:
            if not f.is_file():
                raise BondError(f"not a file: {f}")
            readable(f.name)  # before reading any bytes
        files = [NewSource(args.name or f.name, f.read_bytes(), str(f.resolve())) for f in args.files]
        with open_local(home_of(args), args.workspace) as bond:
            workspace = bond.workspace.name
            try:
                result = add_sources(bond, files, update=args.update)
            except BondError as e:
                hint = "; use --as for another name, or --update" if "already has a document" in str(e) else ""
                raise BondError(f"{e}{hint}") from None
            for f, done in zip(args.files, result.added, strict=True):
                print(f"added {f} to {workspace!r} as {done.document!r}" if done.added else
                      f"{done.document!r} in {workspace!r} already is {f}; nothing added")
            if result.note:
                print(result.note)
            for f in files:
                if not spec(readable(f.name)).has_text:
                    print(f"  transcribe it with: bond transcribe {args.workspace} {f.name!r}")
        return 0
    except BondError as e:
        print(f"bond add: {e}", file=sys.stderr)
        return 1


def run_transcribe(args: argparse.Namespace) -> int:
    """bond transcribe: a source's transcription by a producer, run here or recorded from its output."""
    from .importers import TRANSCRIPTIONS, transcribers_for
    from .transcription import record, source_of, transcribe, transcription_name

    try:
        if args.producer is not None and args.producer not in TRANSCRIPTIONS:
            raise BondError(f"Bond doesn't know producer {args.producer!r} (it knows {', '.join(TRANSCRIPTIONS)})")
        if args.output is not None and args.producer is None:
            raise BondError("--output is a producer's output: name the producer with --with")
        if args.output is not None and not args.output.is_file():
            raise BondError(f"not a file: {args.output}")
        with open_local(home_of(args), args.workspace) as bond:
            source = source_of(bond, args.document)
            if args.output is not None:
                chosen = TRANSCRIPTIONS[args.producer]
                result = record(bond, args.document, chosen, args.output.read_bytes())
            else:
                runnable = transcribers_for(source.format)
                if args.producer is not None:
                    runnable = [t for t in runnable if t.producer == args.producer]
                if not runnable and args.producer is not None:
                    raise BondError(f"Bond doesn't run {args.producer} on {source.format.value}; run it, and record its "
                                    f"output with --with {args.producer} --output FILE")
                if not runnable:
                    raise BondError(f"Bond runs no producer that reads {source.format.value}; run one, and record its "
                                    "output with --with PRODUCER --output FILE")
                chosen = runnable[0]
                result = transcribe(bond, args.document, chosen)
            name = transcription_name(args.document, chosen.producer)
            print(f"transcribed {args.document!r} as {name!r}" if result.advanced else
                  f"{name!r} already is this transcription; nothing written")
        return 0
    except BondError as e:
        print(f"bond transcribe: {e}", file=sys.stderr)
        return 1


def run_link(args: argparse.Namespace) -> int:
    """bond link: let documents be imported from one workspace in the home into another, or stop that."""
    try:
        home = home_of(args)
        if args.remove:
            changed = home.unlink(args.workspace, args.source)
            print(f"documents in {args.source!r} can no longer be imported into {args.workspace!r}" if changed else
                  f"{args.workspace!r} wasn't linked to {args.source!r}")
        else:
            changed = home.link(args.workspace, args.source)
            print(f"documents in {args.source!r} can be imported into {args.workspace!r}"
                  + ("" if changed else " (already linked)"))
        return 0
    except BondError as e:
        print(f"bond link: {e}", file=sys.stderr)
        return 1


def run_grant(args: argparse.Namespace) -> int:
    """bond grant: let a server configuration's agent work in a workspace, or stop that; for hosts with no view."""
    try:
        home = home_of(args)
        if args.remove:
            changed = home.revoke(args.config, args.workspace)
            print(f"{args.config!r} can no longer work in {args.workspace!r}" if changed else
                  f"{args.config!r} couldn't work in {args.workspace!r}")
        else:
            changed = home.grant(args.config, args.workspace)
            print(f"{args.config!r} can work in {args.workspace!r}" + ("" if changed else " (already could)"))
        return 0
    except BondError as e:
        print(f"bond grant: {e}", file=sys.stderr)
        return 1


def run_workspaces(args: argparse.Namespace) -> int:
    """bond workspaces: the workspaces in the home, what each may import from, and who may work in it."""
    try:
        home = home_of(args)
        names = home.names()
        if not names:
            print(f"no workspaces in {home.root}; create one with: bond new NAME")
        for name in names:
            notes = [f"imports from: {', '.join(links)}" for links in [home.links(name)] if links]
            notes += [f"granted to: {', '.join(grantees)}" for grantees in [home.grantees(name)] if grantees]
            print(name + (f"  ({'; '.join(notes)})" if notes else ""))
        return 0
    except BondError as e:
        print(f"bond workspaces: {e}", file=sys.stderr)
        return 1


def run_import(args: argparse.Namespace) -> int:
    """bond import: bring one document's head in, and say what came and what stayed outside."""
    try:
        home = home_of(args)
        # A directory to import into is made if new, as it always was; a name must already be in the home.
        target = Bond.local(Path(args.into)) if is_path(args.into) else open_local(home, args.into)
        with open_local(home, args.source) as source, target as into:
            name = args.name or args.document
            existing = into.filing.document(name)
            result = into.import_document(source, args.document, name=name, depth=args.depth, limit=args.limit,
                                          base=existing.head if existing else None)
            print(f"imported {args.document!r} from {source.workspace.name!r} into {into.workspace.name!r} as "
                  f"{name!r} with its provenance: version {result.version[:12]}…")
            if outside := into.frontier():
                print(f"{len(outside)} version(s) it rests on stayed outside; bring them in with --depth and --limit")
            return 0
    except (BondError, ValueError) as e:
        print(f"bond import: {e}", file=sys.stderr)
        return 1


# --- serving -----------------------------------------------------------------------


def run_view(args: argparse.Namespace) -> int:
    try:
        with view_workspaces(args) as offered:
            return serve(offered, args)
    except (BondError, ImportError) as e:
        print(f"bond view: {e}", file=sys.stderr)
        return 1


@contextmanager
def view_workspaces(args: argparse.Namespace) -> Generator[Fixed]:
    """The workspaces bond view offers, by name, opened as they are and closed after: each --workspace or --name
    given, or with neither every workspace in the home. Nothing is created."""
    with ExitStack() as stack:
        if args.postgres:
            stores = [stack.enter_context(open_postgres(args, name)) for name in args.name or [None]]
        else:
            home = home_of(args)
            values = args.workspace or home.names()
            if not values:
                raise BondError(f"no workspaces in {home.root}; create one with bond new NAME, or name a directory "
                                "with --workspace")
            stores = [stack.enter_context(open_local(home, value)) for value in values]
        offered: dict[str, Bond] = {}
        for bond in stores:
            if bond.workspace.name in offered:
                raise BondError(f"two workspaces given are named {bond.workspace.name!r}; the viewer names "
                                "workspaces by name")
            offered[bond.workspace.name] = bond
        yield Fixed(offered)


def serve(offered: Workspaces, args: argparse.Namespace) -> int:
    uvicorn = _require("uvicorn", "bond[view]")
    from .http.server import create_app

    if not _is_loopback(args.host):
        print(f"bond view: listening on {args.host}, not localhost. The viewer has no authentication: anyone who "
              "can reach it can read every offered workspace.", file=sys.stderr)
    url = f"http://{'localhost' if _is_loopback(args.host) else args.host}:{args.port}"
    app = create_app(offered, graph_limit=args.graph_limit)
    print(f"Bond viewer on {url} (Ctrl-C to stop)")
    if not args.no_browser:
        threading.Timer(0.8, webbrowser.open, (url,)).start()
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


def run_mcp(args: argparse.Namespace) -> int:
    """bond mcp: serve the authoring tools over stdio, which carries the protocol, so problems go to stderr."""
    try:
        _require("mcp", "bond[mcp]")
        from .local.grants import GrantedWorkspaces
        from .local.grants import create_server as grant_server
        from .mcp.server import create_server
        from .mcp.view import packaged_view

        with mcp_served(args) as served:
            serve = grant_server if isinstance(served.workspaces, GrantedWorkspaces) else create_server
            serve(served, view=packaged_view()).run("stdio")
        return 0
    except (BondError, ImportError) as e:
        print(f"bond mcp: {e}", file=sys.stderr)
        return 1


@contextmanager
def mcp_served(args: argparse.Namespace) -> Generator[Served]:
    """What bond mcp serves: its workspaces, bound to the agent's policy and the import budgets given."""
    from .mcp.server import Served

    with mcp_workspaces(args) as workspaces:
        yield Served(workspaces, import_depth=args.import_depth, import_limit=args.import_limit)


@contextmanager
def mcp_workspaces(args: argparse.Namespace) -> Generator[Workspaces]:
    """The workspaces bond mcp serves, by name: each workspace the arguments name, with the workspaces it may import
    from (its links in the home, and every --import-from), or in grant mode what was granted; every one closed
    after.

    A directory with no workspace in it becomes a new one, as before; a name must already be in the home, since
    making a workspace is a person's decision (bond new). Each workspace is opened once, however often it's named.
    """
    if args.grants:
        from .local.grants import GrantedWorkspaces

        granted = GrantedWorkspaces(home_of(args), args.grants)
        try:
            yield granted
        finally:
            granted.close()
        return
    with ExitStack() as stack:
        if args.postgres:
            bond = stack.enter_context(open_postgres(args, args.name))
            sources = [stack.enter_context(open_postgres(args, source)) for source in args.import_from]
            name = bond.workspace.name
            yield Fixed({name: bond}, {name: {s.workspace.name: s for s in sources}})
            return
        home = home_of(args)
        opened: dict[Path, Bond] = {}

        def open_once(value: str, create: bool = False) -> Bond:
            path = home.resolve(value).resolve()
            if path not in opened:
                opened[path] = stack.enter_context(open_local(home, value, create=create))
            return opened[path]

        extra = [open_once(source) for source in args.import_from]
        served: dict[str, Bond] = {}
        links: dict[str, dict[str, Bond]] = {}
        for value in args.workspace:
            bond = open_once(value, create=is_path(value))
            name = bond.workspace.name
            if name in served:
                raise BondError(f"two workspaces given are named {name!r}; an agent names workspaces by name")
            linked = [] if is_path(value) else [open_once(n) for n in home.links(name)]
            sources = {s.workspace.name: s for s in (*linked, *extra) if s is not bond}
            served[name], links[name] = bond, sources
        yield Fixed(served, links)


# --- opening workspaces ------------------------------------------------------------


@contextmanager
def open_local(home: Home, value: str | os.PathLike[str], create: bool = False) -> Generator[Bond]:
    """A local workspace: a directory (a value with a separator in it), or a name in the home, closed after.

    With create, a directory with no workspace in it becomes a new one; a name is never created here.
    """
    text = os.fspath(value)
    path = home.resolve(text)
    if is_path(text):
        if not path.is_dir():
            raise BondError(f"no workspace at {path}")
    else:
        home.require(text)
        create = False
    with Bond.local(path, create=create) as bond:
        yield bond


@contextmanager
def open_postgres(args: argparse.Namespace, name: str | None) -> Generator[Bond]:
    """A workspace in the Postgres store the arguments name (the only one, if name is None), with the blobs they
    name, closed after. Nothing is created."""
    from .storage.postgres import PostgresStore

    pools = _require("psycopg_pool", "psycopg[binary,pool]")
    with pools.ConnectionPool(args.postgres, min_size=1, max_size=8, open=True) as pool:
        with Bond(PostgresStore(pool, create=False), open_blobs(args), name, create=False) as bond:
            yield bond


def open_blobs(args: argparse.Namespace) -> Blobs:
    if args.blobs:
        from .storage.local import LocalBlobs

        if not args.blobs.is_dir():
            raise BondError(f"no blob directory at {args.blobs}")
        return LocalBlobs(args.blobs)
    from .storage.s3 import S3Blobs

    client = _require("boto3", "boto3").client("s3", endpoint_url=args.s3_endpoint)
    return S3Blobs(client, args.s3_bucket, args.s3_prefix)


def _require(module: str, package: str) -> ModuleType:
    """Import a module that only some commands need, or say what to install."""
    try:
        return importlib.import_module(module)
    except ImportError:
        raise ImportError(f"this needs {module}; install it with: pip install '{package}'") from None


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:  # ipaddress only says a string isn't an address by raising
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


if __name__ == "__main__":
    sys.exit(main())
