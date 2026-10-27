# A private Home Assistant

ElevenLabs calls one path in your home: `/api/webhook/<id>`. Nothing else needs to be public. If
Home Assistant sits on a VPN or a mesh network, or its external URL works only inside your own
network, put a reverse proxy on the internet that forwards that path to it.

## Point the integration at the proxy

Enter the proxy's URL as the **Webhook URL**, during setup or later under **Agent settings** in the
panel. The field takes an `https://` URL with no path, such as `https://hooks.example.com`. The
tools then call the proxy; Home Assistant keeps its own URLs. **Back to the Home Assistant URL**
undoes it.

## Find the full URL

The setup's closing message shows the full URL with its `<id>`, and so does **Full URL** under
**Agent settings**. The panel calls that URL when it opens, and **Test this URL** calls a new one
before you save it.

**Rotate webhook URL** changes the `<id>`, and so does reinstalling the integration. Update the
proxy after either.

## Caddy

```caddy
hooks.example.com {
    handle /api/webhook/<id> {
        reverse_proxy http://<home-assistant-address>:8123 {
            header_up -X-Forwarded-For
        }
    }
    handle {
        respond 404
    }
}
```

Use `https://` in `reverse_proxy` if your Home Assistant has its own certificate. Publishing only this webhook keeps every other webhook private. Removing `X-Forwarded-For` spares
you a `trusted_proxies` entry in Home Assistant; without it, Home Assistant refuses requests from an
untrusted proxy.

## Nabu Casa

Not tested yet. If your Nabu Casa URL is not your external URL, the field starts empty: enter
`https://<id>.ui.nabu.casa`, then **Test this URL**.

## The Webhook reachable sensor

The **Webhook reachable** sensor calls the webhook URL and shows *OK* when it answers. The `<id>`
exists only once setup ends, so the sensor shows *Problem* until the proxy forwards it.
