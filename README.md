# emby-icy-proxy

A tiny TCP proxy that makes Shoutcast streams playable by HTTP-strict clients like Emby and Jellyfin.

## Why

Shoutcast servers respond with `ICY 200 OK` instead of `HTTP/1.0 200 OK`. This is not valid HTTP, and clients that go through a strict HTTP parser (Emby's transcoder, for one) refuse to read the response. The fix is to sit a small proxy in front of the stream and rewrite that one line.

## How

The proxy listens on `:8080`. Point your client at it with the real Shoutcast URL stuck in the path:

```
http://icy-proxy:8080/http://stream.example.com:8000/;stream.mp3
```

It connects upstream, rewrites the `ICY ` status line into `HTTP/1.0 `, and streams the rest of the bytes through unchanged. It also asks the upstream server not to inject ICY metadata into the audio (which would otherwise confuse the transcoder).

## Run it

```sh
docker compose up -d
```

## Use it with Emby

In Emby, add a radio station ([howto](https://emby.media/support/articles/Strm-Files.html)) with a stream URL of:

```
http://icy-proxy:8080/http://your-shoutcast-host:8000/;stream.mp3
```

If Emby and the proxy are on the same Docker network, `icy-proxy` resolves by service name. Otherwise use the host IP and publish port 8080.

## Notes

- HTTP only (no TLS upstream). Almost all Shoutcast servers are plain HTTP anyway.
- Shoutcast mount points often look like `/;stream.mp3` — the semicolon is part of the path and is preserved correctly.
- One Python process handles a dozen-ish listeners without breaking a sweat.
