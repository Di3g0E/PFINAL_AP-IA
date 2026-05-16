"use client";

import { useEffect, useRef, useState, useCallback } from "react";

/**
 * Captura biométrica: foto (single-frame) o vídeo (3-5 s).
 *
 * Props:
 * - `mode`: "photo" | "video" — determina el tipo de captura.
 * - `onCapture(blob)` — entrega un Blob JPEG (modo photo) o WebM (modo video).
 * - `videoDurationMs` — duración de la grabación de vídeo (default 3500ms).
 *
 * En modo vídeo usa `MediaRecorder` con codec VP8/WebM. Esto lo soportan
 * todos los navegadores modernos en HTTPS/localhost.
 *
 * Requiere HTTPS o localhost (los navegadores bloquean getUserMedia en HTTP).
 */

type CaptureMode = "photo" | "video";

export function WebcamCapture({
  onCapture,
  mode = "video",
  videoDurationMs = 3500,
}: {
  onCapture: (blob: Blob) => void;
  mode?: CaptureMode;
  videoDurationMs?: number;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);

  const [error, setError] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [snapshot, setSnapshot] = useState<string | null>(null);
  const [recording, setRecording] = useState(false);
  const [recordingProgress, setRecordingProgress] = useState(0);
  const [captured, setCaptured] = useState(false);

  useEffect(() => {
    let active = true;
    navigator.mediaDevices
      .getUserMedia({ video: { width: 640, height: 480 }, audio: false })
      .then((stream) => {
        if (!active) {
          stream.getTracks().forEach((t) => t.stop());
          return;
        }
        streamRef.current = stream;
        if (videoRef.current) {
          videoRef.current.srcObject = stream;
          setReady(true);
        }
      })
      .catch((e: Error) => {
        setError(`No se pudo acceder a la cámara: ${e.message}`);
      });

    return () => {
      active = false;
      // Detener grabación si estaba activa
      if (recorderRef.current && recorderRef.current.state !== "inactive") {
        recorderRef.current.stop();
      }
      streamRef.current?.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    };
  }, []);

  // --- Modo foto (legacy) ---
  const handleCapturePhoto = useCallback(() => {
    if (!videoRef.current || !canvasRef.current) return;
    const video = videoRef.current;
    const canvas = canvasRef.current;
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.drawImage(video, 0, 0);
    setSnapshot(canvas.toDataURL("image/jpeg", 0.9));
    setCaptured(true);
    canvas.toBlob(
      (blob) => {
        if (blob) onCapture(blob);
      },
      "image/jpeg",
      0.9,
    );
  }, [onCapture]);

  // --- Modo vídeo (E3) ---
  const handleStartRecording = useCallback(() => {
    if (!streamRef.current) return;

    chunksRef.current = [];
    setRecording(true);
    setRecordingProgress(0);
    setCaptured(false);

    // Elegir codec soportado por el navegador
    const mimeType = MediaRecorder.isTypeSupported("video/webm;codecs=vp9")
      ? "video/webm;codecs=vp9"
      : MediaRecorder.isTypeSupported("video/webm;codecs=vp8")
        ? "video/webm;codecs=vp8"
        : "video/webm";

    const recorder = new MediaRecorder(streamRef.current, {
      mimeType,
      videoBitsPerSecond: 1_000_000, // 1 Mbps — suficiente para caras
    });
    recorderRef.current = recorder;

    recorder.ondataavailable = (e) => {
      if (e.data.size > 0) chunksRef.current.push(e.data);
    };

    recorder.onstop = () => {
      setRecording(false);
      setRecordingProgress(100);
      const blob = new Blob(chunksRef.current, { type: "video/webm" });
      if (blob.size > 0) {
        onCapture(blob);
        setCaptured(true);
      }
    };

    recorder.start(100); // timeslice=100ms para ondataavailable más frecuente

    // Barra de progreso animada
    const startTime = Date.now();
    const progressInterval = setInterval(() => {
      const elapsed = Date.now() - startTime;
      const pct = Math.min(100, (elapsed / videoDurationMs) * 100);
      setRecordingProgress(pct);
      if (elapsed >= videoDurationMs) {
        clearInterval(progressInterval);
      }
    }, 50);

    // Detener automáticamente tras videoDurationMs
    setTimeout(() => {
      clearInterval(progressInterval);
      if (recorder.state !== "inactive") {
        recorder.stop();
      }
    }, videoDurationMs);
  }, [onCapture, videoDurationMs]);

  if (error) {
    return (
      <div className="card border-red-300 bg-red-50 text-red-800 dark:bg-red-950/30">
        <p className="font-medium">Cámara no disponible</p>
        <p className="text-sm">{error}</p>
        <p className="mt-2 text-xs text-slate-600 dark:text-slate-400">
          Verifica que tu navegador tenga permiso para acceder a la cámara y
          que estás accediendo por HTTPS o localhost.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="relative overflow-hidden rounded-md bg-black">
        <video
          ref={videoRef}
          autoPlay
          playsInline
          muted
          className="block w-full"
        />
        {/* Barra de progreso durante grabación de vídeo */}
        {recording && (
          <div className="absolute bottom-0 left-0 right-0 h-1.5 bg-black/40">
            <div
              className="h-full bg-red-500 transition-all duration-100 ease-linear"
              style={{ width: `${recordingProgress}%` }}
            />
          </div>
        )}
        {/* Indicador de grabación */}
        {recording && (
          <div className="absolute top-3 left-3 flex items-center gap-2">
            <span className="relative flex h-3 w-3">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-red-400 opacity-75" />
              <span className="relative inline-flex h-3 w-3 rounded-full bg-red-500" />
            </span>
            <span className="text-xs font-medium text-white drop-shadow">
              Grabando...
            </span>
          </div>
        )}
      </div>
      <canvas ref={canvasRef} className="hidden" />
      <div className="flex items-center gap-3">
        {mode === "photo" ? (
          <button
            type="button"
            className="btn-primary"
            disabled={!ready}
            onClick={handleCapturePhoto}
          >
            {snapshot ? "Capturar de nuevo" : "Capturar foto"}
          </button>
        ) : (
          <button
            type="button"
            className="btn-primary"
            disabled={!ready || recording}
            onClick={handleStartRecording}
          >
            {recording
              ? "Grabando..."
              : captured
                ? "Grabar de nuevo"
                : "Grabar vídeo (3s)"}
          </button>
        )}
        {captured && (
          <span className="text-sm text-green-700 dark:text-green-400">
            ✓ {mode === "photo" ? "foto" : "vídeo"} listo
          </span>
        )}
      </div>
      {mode === "photo" && snapshot && (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={snapshot}
          alt="Captura"
          className="h-32 w-auto rounded-md border border-slate-300 dark:border-slate-700"
        />
      )}
    </div>
  );
}
