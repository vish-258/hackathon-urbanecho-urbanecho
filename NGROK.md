# Ngrok removed — local-only operation

On 9 October 2026, the public ngrok tunnel was stopped at the user’s request. The project-specific agent binary, saved account connection configuration, and tunnel runtime artifacts were removed. No ngrok startup service was configured.

Use **http://localhost:8000/app** on this Mac. The backend remains bound to `127.0.0.1:8000`; the database and worker have no published host ports. Saved recordings, devices, locations, incidents, thresholds, and administrator credentials remain intact.

The old public address no longer forwards to this application. The ngrok account itself was not deleted. For local operation, see [APPLICATION.md](APPLICATION.md).
