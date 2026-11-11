# Configuration

## Where files go

Every folder can be changed in **Settings → Storage**. The folder picker also takes a pasted path, network paths
included.

| | Windows | macOS | Linux |
|---|---|---|---|
| Exports | `Documents\OpenAudioFix\Exports` | `~/Documents/OpenAudioFix/Exports` | `~/Documents/OpenAudioFix/Exports` |
| Work data | `%LOCALAPPDATA%\OpenAudioFix\data` | `~/Library/Application Support/OpenAudioFix` | `~/.local/share/openaudiofix` |
| Models | `%LOCALAPPDATA%\OpenAudioFix\models` | `~/.cache/openaudiofix/models` | `~/.cache/openaudiofix/models` |

Moving a folder copies it to an empty folder, leaves the old one in place and applies after a restart.

## Exporting

**Export** saves into the project's own folder if it has one, otherwise the default export folder. It never
overwrites a file: a number is added. Each file and a **Download all (zip)** are also offered in the browser. Choose
**Download only** in Settings to save nothing on disk.

In Docker there is no default export folder, and the folder picker sees only the work data unless you mount more and
list them in `AFIX_BROWSE_ROOTS`. Folders can be chosen, and **Open export folder** is offered, only on the computer
running the server.

## Environment variables

| Variable | Effect |
|---|---|
| `AFIX_PORT` | Server port. Default `8765`. |
| `AFIX_DATA` | Work data folder. Fixes it at startup, so it cannot be changed in the UI. |
| `AFIX_MODELS` | Models folder. Fixes it at startup, so it cannot be changed in the UI. |
| `AFIX_ALLOWED_HOSTS` | Extra host names the server answers to, comma-separated. |
| `AFIX_BROWSE_ROOTS` | Folders the folder picker can see in a container. |
| `AFIX_OPEN_FOLDER` | `0` hides **Open export folder**, `1` forces it on. |
| `AFIX_WEB_DIST` | Folder of the built web UI when running from a clone. |

## Reaching the UI under another name

The server only answers requests addressed to `127.0.0.1`, `localhost` or `[::1]`, and refuses changes sent from
other websites. This blocks DNS-rebinding and cross-site attacks on your recordings. Behind a reverse proxy or a name
of your own, such as `studio.lan`, list that name in `AFIX_ALLOWED_HOSTS`.

There are no user accounts. Do not expose the port to a network you do not trust.

## Background repair on a native install

**Modules & models → Background repair** finds the GPU with `nvidia-smi`, shows the download size, installs the
packages into the same Python environment, downloads the MossFormer2 model, runs a self-test and starts the GPU
worker. It takes about 6 GB of disk. The same from a terminal:

```bash
afix gpu install      # afix gpu status | test | remove
```

The install only adds packages. numpy, scipy and soundfile stay at the pinned versions, so CPU modules give the same
results with or without the GPU stack.
