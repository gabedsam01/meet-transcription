// recorder.js
//
// Audio capture + encoding helper used inside the offscreen document.
//
// Why an offscreen document? MV3 service workers have no DOM and cannot use
// MediaRecorder / getUserMedia. We host this code in an offscreen document
// (see offscreen.html / offscreen.js) where those Web APIs are available.
//
// This module knows how to:
//   1. Turn a tabCapture streamId (produced by chrome.tabCapture.getMediaStreamId
//      in the service worker) into a live MediaStream of the TAB audio.
//   2. Optionally capture the microphone and MIX it with the tab audio using a
//      single AudioContext, so the resulting recording has both voices.
//   3. Record everything as WebM/Opus via MediaRecorder.
//
// It is intentionally framework-free and keeps no global state beyond the single
// TabRecorder instance the offscreen document owns.

export const RECORDING_MIME_TYPE = "audio/webm;codecs=opus";

/**
 * Acquire the tab audio MediaStream from a tabCapture media stream id.
 *
 * The service worker calls chrome.tabCapture.getMediaStreamId({targetTabId}) to
 * mint the id (that call requires the popup user gesture). Here, inside the
 * offscreen document, we exchange the id for an actual stream.
 *
 * @param {string} streamId
 * @returns {Promise<MediaStream>}
 */
export async function getTabAudioStream(streamId) {
  if (!streamId) {
    throw new Error("streamId ausente para captura da aba.");
  }
  // The chromeMediaSource:"tab" constraints are a Chrome-specific shape, hence
  // the cast-like object literal rather than standard MediaTrackConstraints.
  return navigator.mediaDevices.getUserMedia({
    audio: {
      mandatory: {
        chromeMediaSource: "tab",
        chromeMediaSourceId: streamId,
      },
    },
    video: false,
  });
}

/**
 * Acquire the microphone stream. Only call this when the user opted in.
 * @returns {Promise<MediaStream>}
 */
export async function getMicStream() {
  return navigator.mediaDevices.getUserMedia({ audio: true, video: false });
}

/**
 * Records tab audio (and optionally mic) to a single WebM/Opus blob.
 *
 * Lifecycle:
 *   const rec = new TabRecorder();
 *   await rec.start(streamId, { withMic: true });
 *   ... user talks ...
 *   const { blob, durationSeconds } = await rec.stop();
 */
export class TabRecorder {
  /**
   * @param {Object} [deps] - injectable dependencies for testing.
   * @param {(id:string)=>Promise<MediaStream>} [deps.getTabStream]
   * @param {()=>Promise<MediaStream>} [deps.getMicStream]
   * @param {new (...args:any[])=>any} [deps.AudioContextCtor]
   * @param {new (...args:any[])=>any} [deps.MediaRecorderCtor]
   */
  constructor(deps = {}) {
    this._getTabStream = deps.getTabStream || getTabAudioStream;
    this._getMicStream = deps.getMicStream || getMicStream;
    this._AudioContextCtor =
      deps.AudioContextCtor ||
      (typeof AudioContext !== "undefined" ? AudioContext : undefined);
    this._MediaRecorderCtor =
      deps.MediaRecorderCtor ||
      (typeof MediaRecorder !== "undefined" ? MediaRecorder : undefined);

    this._mediaRecorder = null;
    this._chunks = [];
    this._sourceStreams = [];
    this._audioContext = null;
    this._startedAtMs = 0;
    this._stoppedAtMs = 0;
    this._tabPlaybackNode = null;
  }

  /** @returns {boolean} */
  get isRecording() {
    return Boolean(this._mediaRecorder && this._mediaRecorder.state === "recording");
  }

  /**
   * Begin recording.
   * @param {string} streamId - the tabCapture media stream id.
   * @param {Object} [options]
   * @param {boolean} [options.withMic=false] - also capture and mix the mic.
   * @param {number} [options.timesliceMs=1000] - MediaRecorder chunk interval.
   */
  async start(streamId, { withMic = false, timesliceMs = 1000 } = {}) {
    if (this.isRecording) {
      throw new Error("Já existe uma gravação em andamento.");
    }
    if (!this._MediaRecorderCtor) {
      throw new Error("MediaRecorder indisponível neste contexto.");
    }

    const tabStream = await this._getTabStream(streamId);
    this._sourceStreams.push(tabStream);

    let recordStream = tabStream;

    if (withMic) {
      // Capture the mic; if it fails (permission denied), we keep tab-only
      // rather than aborting the whole recording.
      let micStream = null;
      try {
        micStream = await this._getMicStream();
        this._sourceStreams.push(micStream);
      } catch (err) {
        // Surface a friendly note but continue with tab audio only.
        this._micError = "Microfone indisponível; gravando apenas o áudio da aba.";
      }
      recordStream = this._mix(tabStream, micStream);
    }

    this._chunks = [];
    this._mediaRecorder = new this._MediaRecorderCtor(recordStream, {
      mimeType: RECORDING_MIME_TYPE,
    });
    this._mediaRecorder.ondataavailable = (event) => {
      if (event.data && event.data.size > 0) {
        this._chunks.push(event.data);
      }
    };
    this._startedAtMs = Date.now();
    this._mediaRecorder.start(timesliceMs);
  }

  /**
   * Mix tab + mic streams into a single recording stream using one AudioContext.
   * When mic is null (capture failed), returns the tab stream unchanged.
   * @param {MediaStream} tabStream
   * @param {MediaStream|null} micStream
   * @returns {MediaStream}
   */
  _mix(tabStream, micStream) {
    if (!micStream) {
      return tabStream;
    }
    if (!this._AudioContextCtor) {
      // No AudioContext (unexpected); fall back to tab audio only.
      return tabStream;
    }
    const ctx = new this._AudioContextCtor();
    this._audioContext = ctx;
    const destination = ctx.createMediaStreamDestination();

    // Route the tab audio both into the recording AND back to the speakers, so
    // capturing the tab does not silence what the user hears.
    const tabSource = ctx.createMediaStreamSource(tabStream);
    tabSource.connect(destination);
    tabSource.connect(ctx.destination);
    this._tabPlaybackNode = tabSource;

    // The mic is recorded but intentionally NOT routed to speakers (no echo).
    const micSource = ctx.createMediaStreamSource(micStream);
    micSource.connect(destination);

    return destination.stream;
  }

  /**
   * Stop recording and assemble the final blob.
   * @returns {Promise<{blob: Blob, durationSeconds: number, micError?: string}>}
   */
  async stop() {
    if (!this._mediaRecorder) {
      throw new Error("Nenhuma gravação para finalizar.");
    }

    const blob = await new Promise((resolve) => {
      this._mediaRecorder.onstop = () => {
        resolve(new Blob(this._chunks, { type: RECORDING_MIME_TYPE }));
      };
      if (this._mediaRecorder.state !== "inactive") {
        this._mediaRecorder.stop();
      } else {
        resolve(new Blob(this._chunks, { type: RECORDING_MIME_TYPE }));
      }
    });

    this._stoppedAtMs = Date.now();
    const durationSeconds = Math.max(
      0,
      Math.round((this._stoppedAtMs - this._startedAtMs) / 1000),
    );

    this._cleanup();

    return { blob, durationSeconds, micError: this._micError };
  }

  /** Release all tracks and the AudioContext. */
  _cleanup() {
    for (const stream of this._sourceStreams) {
      for (const track of stream.getTracks()) {
        try {
          track.stop();
        } catch {
          /* ignore */
        }
      }
    }
    this._sourceStreams = [];
    if (this._audioContext) {
      try {
        this._audioContext.close();
      } catch {
        /* ignore */
      }
      this._audioContext = null;
    }
    this._mediaRecorder = null;
    this._tabPlaybackNode = null;
  }
}
