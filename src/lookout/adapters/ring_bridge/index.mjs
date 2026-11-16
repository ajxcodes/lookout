import { RingApi } from "ring-client-api";

function parseArgs(argv) {
  let cameraId = "";
  let refreshToken = process.env.RING_REFRESH_TOKEN || "";

  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--camera-id" && i + 1 < argv.length) {
      cameraId = argv[i + 1];
      i += 1;
    } else if (arg.startsWith("--camera-id=")) {
      cameraId = arg.slice("--camera-id=".length);
    } else if (arg === "--refresh-token" && i + 1 < argv.length) {
      refreshToken = argv[i + 1];
      i += 1;
    } else if (arg.startsWith("--refresh-token=")) {
      refreshToken = arg.slice("--refresh-token=".length);
    }
  }

  return { cameraId, refreshToken };
}

async function main() {
  const { cameraId, refreshToken } = parseArgs(process.argv.slice(2));
  if (!cameraId) {
    console.error("Missing required --camera-id argument");
    process.exit(1);
  }
  if (!refreshToken) {
    console.error("Missing required --refresh-token argument or RING_REFRESH_TOKEN env var");
    process.exit(1);
  }

  const ringApi = new RingApi({ refreshToken });
  const cameras = await ringApi.getCameras();
  const camera = cameras.find(
    (cam) =>
      String(cam.id) === String(cameraId) ||
      String(cam.data?.device_id || "") === String(cameraId) ||
      cam.name === cameraId,
  );

  if (!camera) {
    console.error(`Ring camera '${cameraId}' not found`);
    process.exit(1);
  }

  const session = await camera.streamVideo({
    output: ["-f", "mpegts", "-c:v", "copy", "-an", "pipe:1"],
    stdoutCallback: (data) => process.stdout.write(data),
  });

  const stop = () => {
    try {
      session.stop();
    } catch {
      // Ignore errors during shutdown
    }
    process.exit(0);
  };

  process.on("SIGINT", stop);
  process.on("SIGTERM", stop);

  session.onCallEnded.subscribe(() => {
    process.exit(0);
  });
}

main().catch((err) => {
  console.error(err?.stack || String(err));
  process.exit(1);
});
