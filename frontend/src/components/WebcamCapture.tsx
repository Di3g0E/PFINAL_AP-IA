"use client";

import { useEffect, useRef, useState } from "react";

/**
 * Captura una imagen de la webcam del usuario.
 *
 * - Pide permiso (`navigator.mediaDevices.getUserMedia`).
 * - Renderiza el stream en un `<video>`.
 * - Al pulsar "Capturar foto", dibuja el frame en un `<canvas>` oculto y
 *   entrega un `Blob` JPEG al callback `onCapture`.
 * - Libera el stream al desmontar.
 *
 * Requiere HTTPS o localhost (los navegadores bloquean getUserMedia en HTTP).
 */
export function WebcamCapture({
  onCapture,
}: {
  onCapture: (blob: Blob) => void;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [snapshot, setSnapshot] = useState<string | null>(null);

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
      streamRef.current?.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    };
  }, []);

  const handleCapture = () => {
    if (!videoRef.current || !canvasRef.current) return;
    const video = videoRef.current;
    const canvas = canvasRef.current;
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.drawImage(video, 0, 0);
    setSnapshot(canvas.toDataURL("image/jpeg", 0.9));
    canvas.toBlob(
      (blob) => {
        if (blob) onCapture(blob);
      },
      "image/jpeg",
      0.9,
    );
  };

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
      <div className="overflow-hidden rounded-md bg-black">
        <video
          ref={videoRef}
          autoPlay
          playsInline
          muted
          className="block w-full"
        />
      </div>
      <canvas ref={canvasRef} className="hidden" />
      <div className="flex items-center gap-3">
        <button
          type="button"
          className="btn-primary"
          disabled={!ready}
          onClick={handleCapture}
        >
          {snapshot ? "Capturar de nuevo" : "Capturar foto"}
        </button>
        {snapshot && (
          <span className="text-sm text-green-700 dark:text-green-400">
            ✓ foto lista
          </span>
        )}
      </div>
      {snapshot && (
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
