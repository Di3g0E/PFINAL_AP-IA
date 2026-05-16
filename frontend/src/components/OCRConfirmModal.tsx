"use client";

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import {
  listUserCategories,
  type ManualTransactionInput,
  type OCRExtracted,
  type UserCategories,
} from "@/lib/api";

type Props = {
  open: boolean;
  initial: OCRExtracted | null;
  previewUrl: string | null;
  submitting: boolean;
  error: string | null;
  onCancel: () => void;
  onConfirm: (input: ManualTransactionInput) => void;
};

export function OCRConfirmModal({
  open,
  initial,
  previewUrl,
  submitting,
  error,
  onCancel,
  onConfirm,
}: Props) {
  const [description, setDescription] = useState("");
  const [date, setDate] = useState("");
  const [amount, setAmount] = useState("");
  const [type, setType] = useState<"Income" | "Expenses">("Expenses");
  const [areaText, setAreaText] = useState("");
  const [userCats, setUserCats] = useState<UserCategories | null>(null);

  useEffect(() => {
    if (initial) {
      setDescription(initial.description_suggested);
      setDate(initial.date_suggested);
      setAmount(initial.amount);
      setType(initial.type_suggested);
      setAreaText(initial.area_suggested.join(", "));
    }
  }, [initial]);

  useEffect(() => {
    if (open) {
      listUserCategories()
        .then(setUserCats)
        .catch(() => setUserCats(null));
    }
  }, [open]);

  const suggestedCategories = type === "Income"
    ? userCats?.income ?? []
    : userCats?.expenses ?? [];

  if (!open || !initial) return null;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const area = areaText
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    onConfirm({
      description: description.trim(),
      date,
      amount,
      type,
      area: area.length ? area : undefined,
    });
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 backdrop-blur-sm p-4">
      <div className="relative w-full max-w-lg max-h-[90vh] overflow-y-auto rounded-2xl bg-white shadow-2xl border border-slate-200 animate-fadeIn">
        <div className="sticky top-0 bg-white/95 backdrop-blur-sm border-b border-slate-200 px-6 py-4 flex items-center justify-between">
          <div>
            <h2 className="text-lg font-bold text-slate-800">Confirmar gasto</h2>
            <p className="text-xs text-slate-500">
              Revisa los datos extraídos por OCR antes de registrarlos.
            </p>
          </div>
          <button
            type="button"
            onClick={onCancel}
            disabled={submitting}
            className="p-2 rounded-lg hover:bg-slate-100 disabled:opacity-50"
            aria-label="Cerrar"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <form onSubmit={handleSubmit} className="px-6 py-4 space-y-4">
          {previewUrl && (
            <div className="rounded-lg overflow-hidden border border-slate-200 bg-slate-50">
              <img
                src={previewUrl}
                alt="Factura"
                className="max-h-48 w-full object-contain"
              />
            </div>
          )}

          <Input
            label="Descripción"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            required
            disabled={submitting}
            helperText="Edítala para que la categorización automática sea más precisa."
          />

          <div className="grid grid-cols-2 gap-3">
            <Input
              label="Importe (EUR)"
              type="number"
              step="0.01"
              min="0"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              required
              disabled={submitting}
            />
            <Input
              label="Fecha"
              type="date"
              value={date}
              onChange={(e) => setDate(e.target.value)}
              required
              disabled={submitting}
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
                  disabled={submitting}
                  className={`flex-1 py-2 px-4 rounded-lg text-sm font-medium border transition-colors ${
                    type === t
                      ? "bg-blue-600 text-white border-blue-600"
                      : "bg-white text-slate-700 border-slate-300 hover:bg-slate-50"
                  }`}
                >
                  {t === "Expenses" ? "Gasto" : "Ingreso"}
                </button>
              ))}
            </div>
          </div>

          <div className="space-y-2">
            <label className="text-sm font-medium text-slate-700">Categoría (Área)</label>

            {suggestedCategories.length > 0 && (
              <div className="flex flex-wrap gap-1">
                {suggestedCategories.map((c) => {
                  const active = areaText
                    .split(",")
                    .map((s) => s.trim().toLowerCase())
                    .includes(c.name.toLowerCase());
                  return (
                    <button
                      key={c.name}
                      type="button"
                      onClick={() => setAreaText(c.name)}
                      disabled={submitting}
                      className={`px-2 py-0.5 rounded-full text-xs font-medium border transition-colors ${
                        active
                          ? "bg-blue-600 text-white border-blue-600"
                          : "bg-white text-slate-700 border-slate-300 hover:bg-slate-50"
                      }`}
                      title={c.count > 0 ? `${c.count} usos previos` : "Sugerida"}
                    >
                      {c.name}
                      {c.count > 0 && (
                        <span className="ml-1 opacity-70">({c.count})</span>
                      )}
                    </button>
                  );
                })}
              </div>
            )}

            <input
              type="text"
              list="ocr-category-suggestions"
              value={areaText}
              onChange={(e) => setAreaText(e.target.value)}
              disabled={submitting}
              className="flex h-10 w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-600 focus:border-transparent disabled:cursor-not-allowed disabled:opacity-50"
              placeholder={
                suggestedCategories[0]?.name
                  ? `Ej. ${suggestedCategories[0].name}…`
                  : "Escribe una categoría…"
              }
            />
            <datalist id="ocr-category-suggestions">
              {suggestedCategories.map((c) => (
                <option key={c.name} value={c.name} />
              ))}
            </datalist>
            <p className="text-xs text-slate-500">
              Click en un chip para usar una categoría existente, o escribe una
              nueva. Déjala vacía para inferencia automática.
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
              onClick={onCancel}
              disabled={submitting}
              className="flex-1"
            >
              Cancelar
            </Button>
            <Button
              type="submit"
              variant="primary"
              disabled={submitting || !description.trim() || !amount || !date}
              className="flex-1"
            >
              {submitting ? "Registrando…" : "Confirmar y registrar"}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
