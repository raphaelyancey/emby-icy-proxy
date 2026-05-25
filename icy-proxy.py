"""
icy-proxy: a tiny TCP proxy that rewrites Shoutcast's non-standard
'ICY 200 OK' response line into 'HTTP/1.0 200 OK' so HTTP-strict clients
like Emby can consume the stream.

Usage from the client side:
    http://icy-proxy:8080/http://real-shoutcast-host:8000/listen.mp3
"""

import asyncio
import urllib.parse
import logging
import sys

LISTEN_HOST = "0.0.0.0"
LISTEN_PORT = 8080
UPSTREAM_CONNECT_TIMEOUT = 10  # seconds
REQUEST_READ_TIMEOUT = 10      # seconds (slowloris guard)
BUFFER_SIZE = 8192

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("icy-proxy")


async def pump(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
    """Shovel bytes from src to dst until EOF or the client disconnects."""
    try:
        while True:
            chunk = await src.read(BUFFER_SIZE)
            if not chunk:
                return
            dst.write(chunk)
            await dst.drain()
    except (ConnectionResetError, BrokenPipeError):
        # Client went away mid-stream; perfectly normal for radio listeners.
        return


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    peer = writer.get_extra_info("peername")
    upstream_writer = None
    try:
        # --- Parse the incoming request from the client ----------------------
        try:
            request_line = await asyncio.wait_for(
                reader.readline(), timeout=REQUEST_READ_TIMEOUT
            )
        except asyncio.TimeoutError:
            log.warning("timeout reading request from %s", peer)
            return

        if not request_line:
            return

        try:
            method, path, _version = request_line.decode("ascii").split(" ", 2)
        except ValueError:
            writer.write(b"HTTP/1.0 400 Bad Request\r\n\r\n")
            await writer.drain()
            return

        # We accept GET (normal play) and HEAD (Emby probes it sometimes).
        if method not in ("GET", "HEAD"):
            writer.write(b"HTTP/1.0 405 Method Not Allowed\r\n\r\n")
            await writer.drain()
            return

        upstream_url = urllib.parse.unquote(path.lstrip("/"))

        # Drain the rest of the client's headers; we don't forward them.
        while True:
            line = await asyncio.wait_for(
                reader.readline(), timeout=REQUEST_READ_TIMEOUT
            )
            if not line or line in (b"\r\n", b"\n"):
                break

        # urlsplit (not urlparse!) — urlparse would split ';stream.mp3'
        # off as 'params', which mangles Shoutcast mount points.
        parsed = urllib.parse.urlsplit(upstream_url)
        if parsed.scheme != "http" or not parsed.hostname:
            writer.write(b"HTTP/1.0 400 Bad Request\r\n\r\n")
            await writer.drain()
            log.warning("bad upstream url from %s: %r", peer, upstream_url)
            return

        host = parsed.hostname
        port = parsed.port or 80
        up_path = parsed.path or "/"
        if parsed.query:
            up_path += "?" + parsed.query

        # --- Connect upstream -----------------------------------------------
        try:
            upstream_reader, upstream_writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=UPSTREAM_CONNECT_TIMEOUT,
            )
        except (asyncio.TimeoutError, OSError) as e:
            log.warning("upstream connect failed (%s:%s): %s", host, port, e)
            writer.write(b"HTTP/1.0 502 Bad Gateway\r\n\r\n")
            await writer.drain()
            return

        log.info("%s -> %s:%s%s", peer, host, port, up_path)

        # Speak HTTP/1.0; Shoutcast servers don't grok 1.1 niceties.
        # Icy-MetaData: 0 tells the server not to interleave title metadata
        # into the audio bytes (Emby's transcoder would choke otherwise).
        req = (
            f"GET {up_path} HTTP/1.0\r\n"
            f"Host: {host}\r\n"
            f"User-Agent: Mozilla/5.0\r\n"
            f"Icy-MetaData: 0\r\n"
            f"Connection: close\r\n"
            f"\r\n"
        ).encode("ascii")
        upstream_writer.write(req)
        await upstream_writer.drain()

        # --- Rewrite the status line ----------------------------------------
        status = await upstream_reader.readline()
        if not status:
            log.warning("empty response from upstream %s:%s", host, port)
            return

        if status.startswith(b"ICY "):
            status = b"HTTP/1.0 " + status[4:]

        writer.write(status)
        await writer.drain()

        # For HEAD requests we just forward headers and stop.
        if method == "HEAD":
            while True:
                line = await upstream_reader.readline()
                writer.write(line)
                if not line or line in (b"\r\n", b"\n"):
                    break
            await writer.drain()
            return

        # --- Stream the rest verbatim ---------------------------------------
        await pump(upstream_reader, writer)

    except Exception as e:
        log.exception("unexpected error handling %s: %s", peer, e)
    finally:
        if upstream_writer is not None:
            try:
                upstream_writer.close()
            except Exception:
                pass
        try:
            writer.close()
        except Exception:
            pass


async def main() -> None:
    server = await asyncio.start_server(handle, LISTEN_HOST, LISTEN_PORT)
    addrs = ", ".join(str(s.getsockname()) for s in server.sockets)
    log.info("icy-proxy listening on %s", addrs)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
