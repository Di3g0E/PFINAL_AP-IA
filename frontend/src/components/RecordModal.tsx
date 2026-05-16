"use client";

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import {
  classifyArea,
  extractFromImage,
  addManualTransaction,
  type TransactionRecord,
} from "@/lib/api";

type Props = {
  open: boolean;
  onClose: () => void;
  onSuccess: (record: TransactionRecord) => void;
};

export function RecordModal({ open, onClose, onSuccess }: Props) {
  const [description, setDescription] = useState("");
  const [date, setDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [amount, setAmount] = useState("");
  const [type, setType] = useState<"Income" | "Expenses">("Expenses");
  const [areaText, setAreaText] = useState("");

  const [loading, setLoading] = useState(false);
  const [ocrLoading, setOcrLoading] = useState(false);
  const [classifying, setClassifying] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Limpiar campos al abrir
  useEffect(() => {
    if (open) {
      setDescription("");
      setDate(new Date().toISOString().slice(0, 10));
      setAmount("");
      setType("Expenses");
      setAreaText("");
      setError(null);
    }
  }, [open]);

  if (!open) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const area = areaText
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
      const res = await addManualTransaction({
        description: description.trim(),
        date,
        amount,
        type,
        area: area.length ? area : undefined,
      });
      // Puede devolver accepted o pending. Asumimos accepted para el refresco
      if (res.accepted && res.accepted.length > 0) {
        onSuccess(res.accepted[0]);
      } else if (res.pending_review && res.pending_review.length > 0) {
        onSuccess(res.pending_review[0].record);
      } else {
        throw new Error("Transacción rechazada o no guardada.");
      }
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  };

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    setOcrLoading(true);
    setError(null);
    try {
      const extracted = await extractFromImage(file);
      setDescription(extracted.description_suggested);
      setDate(extracted.date_suggested);
      setAmount(extracted.amount);
      setType(extracted.type_suggested);
      setAreaText(extracted.area_suggested.join(", "));
    } catch (err) {
      setError("Error OCR: " + (err as Error).message);
    } finally {
      setOcrLoading(false);
      e.target.value = ""; // clear input
    }
  };

  const handleDescriptionBlur = async () => {
    if (!description.trim() || areaText.trim()) return; // Si ya hay area, no sobreescribir
    setClassifying(true);
    try {
      const cat = await classifyArea(description.trim());
      if (cat) {
        setAreaText(cat);
      }
    } catch (err) {
      console.warn("No se pudo clasificar", err);
    } finally {
      setClassifying(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 backdrop-blur-sm p-4">
      <div className="relative w-full max-w-lg max-h-[90vh] overflow-y-auto rounded-2xl bg-white shadow-2xl border border-slate-200 animate-fadeIn">
        <div className="sticky top-0 bg-white/95 backdrop-blur-sm border-b border-slate-200 px-6 py-4 flex items-center justify-between z-10">
          <div>
            <h2 className="text-lg font-bold text-slate-800">Nuevo Registro Manual</h2>
            <p className="text-xs text-slate-500">
              Añade un ingreso o gasto. Puedes subir una foto para autocompletar.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={loading || ocrLoading}
            className="p-2 rounded-lg hover:bg-slate-100 disabled:opacity-50"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <div className="px-6 py-4 space-y-4">
          <div className="border-2 border-dashed border-slate-300 rounded-xl p-6 text-center hover:bg-slate-50 transition-colors relative">
            <input
              type="file"
              accept="image/*"
              className="absolute inset-0 w-full h-full opacity-0 cursor-pointer disabled:cursor-not-allowed"
              onChange={handleFileChange}
              disabled={loading || ocrLoading}
            />
            <div className="pointer-events-none">
              {ocrLoading ? (
                <div className="flex flex-col items-center text-blue-600">
                  <div className="w-6 h-6 border-2 border-blue-600 border-t-transparent rounded-full animate-spin mb-2"></div>
                  <span className="text-sm font-medium">Analizando recibo (OCR)...</span>
                </div>
              ) : (
                <div className="flex flex-col items-center text-slate-600">
                  <svg className="w-8 h-8 mb-2 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" />
                  </svg>
                  <span className="text-sm font-medium">Click o arrastra el recibo (Opcional)</span>
                  <span className="text-xs text-slate-400 mt-1">Extraerá automáticamente los datos</span>
                </div>
              )}
            </div>
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="relative">
              <Input
                label="Descripción"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                onBlur={handleDescriptionBlur}
                required
                disabled={loading || ocrLoading}
                helperText="Al terminar de escribir, clasificaremos la categoría automáticamente."
              />
              {classifying && (
                <div className="absolute right-3 top-9">
                  <div className="w-4 h-4 border-2 border-slate-300 border-t-blue-600 rounded-full animate-spin"></div>
                </div>
              )}
            </div>

            <div className="grid grid-cols-2 gap-3">
              <Input
                label="Importe (EUR)"
                type="number"
                step="0.01"
                min="0"
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
                required
                disabled={loading || ocrLoading}
              />
              <Input
                label="Fecha"
                type="date"
                value={date}
                onChange={(e) => setDate(e.target.value)}
                required
                disabled={loading || ocrLoading}
              />
            </div>

            <div className="space-y-2">
              <label className="text-sm font-medium text-slate-700">Tipo</label>
              <div className="flex gap-2">
                {(["Expenses", "Income"] as const).map((t) => (
                  <button
                    key={t}
                    type="button"
                    onClick={() => setType(t)}
                    disabled={loading || ocrLoading}
                    className={`flex-1 py-2 px-4 rounded-lg text-sm font-medium border transition-colors ${
                      type === t
                        ? "bg-blue-600 text-white border-blue-600 shadow-sm"
                        : "bg-white text-slate-700 border-slate-300 hover:bg-slate-50"
                    }`}
                  >
                    {t === "Expenses" ? "Gasto" : "Ingreso"}
                  </button>
                ))}
              </div>
            </div>

            <div className="space-y-1">
              <label className="text-sm font-medium text-slate-700">Categoría (Área)</label>
              <input
                type="text"
                list="category-suggestions"
                value={areaText}
                onChange={(e) => setAreaText(e.target.value)}
                disabled={loading || ocrLoading}
                className="flex h-10 w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-600 focus:border-transparent disabled:cursor-not-allowed disabled:opacity-50"
                placeholder="Ej. Software, Transporte..."
              />
              <datalist id="category-suggestions">
                <option value="Comida" />
                <option value="Transporte" />
                <option value="Alojamiento" />
                <option value="Soporte IT" />
                <option value="Software" />
                <option value="Hardware" />
                <option value="Oficina" />
                <option value="Marketing" />
                <option value="Otros" />
              </datalist>
              <p className="text-xs text-slate-500">
                Selecciona, escribe una nueva o deja que se infiera sola.
              </p>
            </div>

            {error && (
              <div className="rounded-lg bg-red-50 border border-red-200 p-3">
                <p className="text-sm text-red-700">{error}</p>
              </div>
            )}

            <div className="flex gap-2 pt-2">
              <Button
                type="button"
                variant="secondary"
                onClick={onClose}
                disabled={loading || ocrLoading}
                className="flex-1"
              >
                Cancelar
              </Button>
              <Button
                type="submit"
                variant="primary"
                disabled={loading || ocrLoading || !description.trim() || !amount || !date}
                className="flex-1"
              >
                {loading ? "Guardando…" : "Guardar Registro"}
              </Button>
            </div>
          </form>
        </div>
      </div>
    </div>
  );
}
