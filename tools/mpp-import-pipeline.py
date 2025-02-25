#!/usr/bin/python3

"""Manifest-Pre-Processor - Pipeline Import

This manifest-pre-processor consumes a manifest on stdin, processes it, and
produces the resulting manifest on stdout.

This tool imports a pipeline from another file and inserts it into a manifest
at the same position the import instruction is located. Sources from the
imported manifest are merged with the existing sources.

Manifest format version "1" and "2" are supported.

The parameters for this pre-processor for format version "1" look like this:

```
...
    "mpp-import-pipeline": {
      "path": "./manifest.json"
    }
...
```

The parameters for this pre-processor for format version "2" look like this:

```
...
    "mpp-import-pipeline": {
      "path": "./manifest.json",
      "id:" "build"
    }
...
```
"""

import argparse
import collections
import contextlib
import json
import os
import sys

from typing import Dict


class State:
    cwd = None                          # CurrentWorkingDirectory for imports

    manifest = None                     # Input/Working Manifest
    manifest_urls = None                # Link to sources URL dict
    manifest_todo = []                  # Array of links to import pipelines


def _manifest_enter(manifest, key, default):
    if key not in manifest:
        manifest[key] = default
    return manifest[key]


def _sort_urls(urls: Dict):
    def get_sort_key(item):
        key = item[1]
        if isinstance(key, dict):
            key = key["url"]
        return key

    if not urls:
        return urls

    urls_sorted = sorted(urls.items(), key=get_sort_key)
    urls.clear()
    urls.update(collections.OrderedDict(urls_sorted))


def _manifest_parse_v1(state, data):
    manifest = data

    # Resolve "sources"."org.osbuild.files"."urls".
    manifest_sources = _manifest_enter(manifest, "sources", {})
    manifest_files = _manifest_enter(manifest_sources, "org.osbuild.files", {})
    manifest_urls = _manifest_enter(manifest_files, "urls", {})

    # Collect import entries in a TO-DO list.
    manifest_todo = []

    # Find the `mpp-import-pipeline` section. We iterate down the buildtrees
    # until we find one. Since an import overrides a possibly existing pipeline
    # only one import needs to be handled (the others would be overridden). We
    # do support multiple, so this can be easily extended in the future.
    current = manifest
    while current:
        if "mpp-import-pipeline" in current:
            manifest_todo.append(current)
            break

        current = current.get("pipeline", {}).get("build")

    # Remember links of interest.
    state.manifest = manifest
    state.manifest_urls = manifest_urls
    state.manifest_todo = manifest_todo


def _manifest_process_v1(state, todo):
    mpp = _manifest_enter(todo, "mpp-import-pipeline", {})
    mpp_path = mpp["path"]

    # Load the to-be-imported manifest.
    with open(os.path.join(state.cwd, mpp_path), "r") as f:
        imp = json.load(f)

    # Resolve keys from the import.
    imp_sources = _manifest_enter(imp, "sources", {})
    imp_files = _manifest_enter(imp_sources, "org.osbuild.files", {})
    imp_urls = _manifest_enter(imp_files, "urls", {})
    imp_pipeline = _manifest_enter(imp, "pipeline", {})

    # We only support importing manifests with URL sources. Other sources are
    # not supported, yet. This can be extended in the future, but we should
    # maybe rather try to make sources generic (and repeatable?), so we can
    # deal with any future sources here as well.
    assert list(imp_sources.keys()) == ["org.osbuild.files"]

    # We import `sources` from the manifest, as well as a pipeline description
    # from the `pipeline` entry. Make sure nothing else is in the manifest, so
    # we do not accidentally miss new features.
    assert list(imp.keys()).sort() == ["pipeline", "sources"].sort()

    # Now with everything imported and verified, we can merge the pipeline back
    # into the original manifest. We take all URLs and merge them in the pinned
    # url-array, and then we take the pipeline and simply override any original
    # pipeline at the position where the import was declared. Lastly, we delete
    # the mpp-import statement.
    state.manifest_urls.update(imp_urls)
    todo["pipeline"] = imp_pipeline
    del(todo["mpp-import-pipeline"])


def _manifest_import_v1(state, src):
    _manifest_parse_v1(state, src)

    for todo in state.manifest_todo:
        _manifest_process_v1(state, todo)


def _manifest_parse_v2(state, manifest):
    todo = []

    pipelines = manifest.get("pipelines", [])

    for pipeline in pipelines:
        current = pipeline.get("mpp-import-pipeline")
        if current:
            todo.append(pipeline)

    state.manifest = manifest
    state.manifest_todo = todo


def _manifest_process_v2(state, todo):
    manifest = state.manifest
    sources = _manifest_enter(manifest, "sources", {})

    mpp = todo["mpp-import-pipeline"]
    path = mpp["path"]

    with open(os.path.join(state.cwd, path), "r") as f:
        imp = json.load(f)

    # merge the sources
    for source, desc in imp.get("sources", {}).items():
        target = sources.get(source)
        if not target:
            # new source, just copy everything
            sources[source] = desc
            continue

        if desc.get("options"):
            options = _manifest_enter(target, "options", {})
            options.update(desc["options"])

        items = _manifest_enter(target, "items", {})
        items.update(desc.get("items", {}))

    # get the pipeline
    pipelines = imp.get("pipelines", [])

    pid = mpp["id"]

    target = None
    for pipeline in pipelines:
        if pipeline["name"] == pid:
            target = pipeline
            break

    if not target:
        raise ValueError(f"Pipeline '{pid}' not found in {path}")

    todo.update(target)
    del(todo["mpp-import-pipeline"])


def _manifest_import_v2(state, src):
    _manifest_parse_v2(state, src)
    for todo in state.manifest_todo:
        _manifest_process_v2(state, todo)


def _main_args(argv):
    parser = argparse.ArgumentParser(description="Generate Test Manifests")

    parser.add_argument(
        "--cwd",
        metavar="PATH",
        type=os.path.abspath,
        default=None,
        help="Current Working Directory for relative import paths",
    )

    return parser.parse_args(argv[1:])


@contextlib.contextmanager
def _main_state(args):
    state = State()
    state.cwd = args.cwd or "."
    yield state


def _main_process(state):
    src = json.load(sys.stdin)
    version = src.get("version", "1")
    if version == "1":
        _manifest_import_v1(state, src)
    elif version == "2":
        _manifest_import_v2(state, src)
    else:
        return 1

    _sort_urls(state.manifest_urls)

    json.dump(state.manifest, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def main() -> int:
    args = _main_args(sys.argv)
    with _main_state(args) as state:
        res = _main_process(state)

    return res


if __name__ == "__main__":
    sys.exit(main())
