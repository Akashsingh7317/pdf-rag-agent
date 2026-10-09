// Records a spoken question in the browser (MediaRecorder). The server turns
// it into text with Whisper. Browsers allow the microphone on https and on
// localhost / 127.0.0.1.

export const voiceSupported = Boolean(navigator.mediaDevices?.getUserMedia && window.MediaRecorder);

const PREFERRED_TYPES = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg"];

export class Recorder {
  async start() {
    this.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const type = PREFERRED_TYPES.find((t) => MediaRecorder.isTypeSupported(t));
    this.recorder = new MediaRecorder(this.stream, type ? { mimeType: type } : undefined);
    this.parts = [];
    this.recorder.ondataavailable = (e) => {
      if (e.data.size) this.parts.push(e.data);
    };
    this.recorder.start();
    this.startedAt = Date.now();
  }

  get seconds() {
    return Math.floor((Date.now() - this.startedAt) / 1000);
  }

  /** Stop and return the recording as a Blob. */
  stop() {
    return new Promise((resolve) => {
      this.recorder.onstop = () => {
        this.release();
        resolve(new Blob(this.parts, { type: this.recorder.mimeType || "audio/webm" }));
      };
      this.recorder.stop();
    });
  }

  cancel() {
    if (this.recorder && this.recorder.state !== "inactive") {
      this.recorder.onstop = () => this.release();
      this.recorder.stop();
    } else {
      this.release();
    }
  }

  release() {
    this.stream?.getTracks().forEach((track) => track.stop());
    this.stream = null;
  }
}

export function fileExtensionFor(mimeType) {
  if (mimeType.includes("mp4")) return "m4a";
  if (mimeType.includes("ogg")) return "ogg";
  return "webm";
}
