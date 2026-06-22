#!/usr/bin/env python3
"""Minimal reproduction: an HTTP operation followed by a trivial subprocess.

Each invocation performs exactly one operation so a poisoned process cannot
contaminate later matrix cells. Besides normal ``urllib`` this can bypass the
macOS system proxy bridge, use ``http.client`` directly, or exercise proxy
discovery without making a network request.
"""

from __future__ import annotations

import json
import signal
import subprocess
import sys
import urllib.request


def main() -> int:
    arguments = sys.argv[1:]
    client = _option(arguments, "--client", "urllib")
    use_accept = "--no-accept" not in arguments
    use_charset = "--no-charset" not in arguments
    parse_json = "--no-json" not in arguments
    request_only = "--request-only" in arguments
    use_posix_spawn = "--posix-spawn" in arguments
    arguments = _remaining_arguments(arguments)
    url_required = client not in {"getproxies", "import-scproxy"}
    if len(arguments) != int(url_required):
        print(
            f"usage: {sys.argv[0]} "
            "[--client urllib|urllib-no-proxy|http-client|getproxies|"
            "httpx|aiohttp|import-scproxy|proxy-bypass|proxy-bypass-connect] "
            "[--no-accept] [--no-charset] "
            "[--no-json] [--request-only] [--posix-spawn] [URL]",
            file=sys.stderr,
        )
        return 2

    headers = {"Accept": "application/json"} if use_accept else {}
    if client == "getproxies":
        proxies = urllib.request.getproxies()
        print(f"proxy_schemes={sorted(proxies)}")
    elif client == "import-scproxy":
        __import__("_scproxy")
        print("imported_scproxy=true")
    elif client == "proxy-bypass":
        bypassed = urllib.request.proxy_bypass(arguments[0])
        print(f"proxy_bypass={bypassed}")
    elif client == "proxy-bypass-connect":
        _proxy_bypass_then_connect(arguments[0])
    elif client == "httpx":
        _perform_httpx_request(arguments[0], headers, parse_json)
    elif client == "aiohttp":
        _perform_aiohttp_request(arguments[0], headers, parse_json)
    else:
        _perform_request(
            arguments[0],
            client=client,
            headers=headers,
            use_charset=use_charset,
            parse_json=parse_json,
            request_only=request_only,
        )

    result = subprocess.run(
        ("/usr/bin/true",),
        check=False,
        close_fds=not use_posix_spawn,
    )
    if result.returncode < 0:
        number = -result.returncode
        print(
            f"returncode={result.returncode} "
            f"signal={number} signal_name={signal.Signals(number).name}"
        )
    else:
        print(f"returncode={result.returncode}")
    return int(result.returncode != 0)


def _perform_request(
    url: str,
    *,
    client: str,
    headers: dict[str, str],
    use_charset: bool,
    parse_json: bool,
    request_only: bool,
) -> None:
    request = urllib.request.Request(url, headers=headers)
    if request_only:
        print(f"request_only=true client={client}")
        return
    if client == "urllib":
        response = urllib.request.urlopen(request, timeout=10)
    elif client == "urllib-no-proxy":
        response = urllib.request.build_opener(
            urllib.request.ProxyHandler({})
        ).open(request, timeout=10)
    elif client == "http-client":
        _perform_http_client_request(url, headers, use_charset, parse_json)
        return
    else:
        raise ValueError(f"unknown client: {client}")
    with response:
        body = response.read()
        charset = (
            response.headers.get_content_charset() or "utf-8"
            if use_charset
            else "utf-8"
        )
        status = response.status
    _describe_response(status, body, charset, client, parse_json)


def _proxy_bypass_then_connect(url: str) -> None:
    import socket
    import urllib.parse

    parsed = urllib.parse.urlsplit(url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    bypassed = urllib.request.proxy_bypass(parsed.hostname)
    with socket.create_connection((parsed.hostname, port), timeout=10):
        pass
    print(f"proxy_bypass={bypassed} tcp_connected=true")


def _perform_httpx_request(
    url: str, headers: dict[str, str], parse_json: bool
) -> None:
    import httpx

    response = httpx.get(url, headers=headers, timeout=10)
    print(
        f"http_status={response.status_code} "
        f"body_bytes={len(response.content)} client=httpx"
    )
    if parse_json:
        print(f"json_type={type(response.json()).__name__}")


def _perform_aiohttp_request(
    url: str, headers: dict[str, str], parse_json: bool
) -> None:
    import asyncio

    asyncio.run(_aiohttp_get(url, headers, parse_json))


async def _aiohttp_get(
    url: str, headers: dict[str, str], parse_json: bool
) -> None:
    import aiohttp

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers, timeout=10) as response:
            body = await response.read()
            print(
                f"http_status={response.status} "
                f"body_bytes={len(body)} client=aiohttp"
            )
            if parse_json:
                payload = json.loads(body.decode(response.charset or "utf-8"))
                print(f"json_type={type(payload).__name__}")


def _perform_http_client_request(
    url: str,
    headers: dict[str, str],
    use_charset: bool,
    parse_json: bool,
) -> None:
    import http.client
    import urllib.parse

    parsed = urllib.parse.urlsplit(url)
    connection_type = (
        http.client.HTTPSConnection
        if parsed.scheme == "https"
        else http.client.HTTPConnection
    )
    connection = connection_type(parsed.hostname, parsed.port, timeout=10)
    path = urllib.parse.urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
    try:
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        body = response.read()
        charset = "utf-8"
        if use_charset:
            charset = response.headers.get_content_charset() or charset
        status = response.status
    finally:
        connection.close()
    _describe_response(status, body, charset, "http-client", parse_json)


def _describe_response(
    status: int,
    body: bytes,
    charset: str,
    client: str,
    parse_json: bool,
) -> None:
    print(f"http_status={status} body_bytes={len(body)} client={client}")
    if parse_json:
        payload = json.loads(body.decode(charset))
        print(f"json_type={type(payload).__name__}")


def _option(arguments: list[str], name: str, default: str) -> str:
    if name not in arguments:
        return default
    position = arguments.index(name)
    try:
        return arguments[position + 1]
    except IndexError as error:
        raise SystemExit(f"{name} requires a value") from error


def _remaining_arguments(arguments: list[str]) -> list[str]:
    remaining: list[str] = []
    skip_next = False
    for item in arguments:
        if skip_next:
            skip_next = False
        elif item == "--client":
            skip_next = True
        elif item not in {
            "--no-accept",
            "--no-charset",
            "--no-json",
            "--posix-spawn",
            "--request-only",
        }:
            remaining.append(item)
    return remaining


if __name__ == "__main__":
    raise SystemExit(main())
