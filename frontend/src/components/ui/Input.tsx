"use client";

import { forwardRef } from "react";
import { cn } from "@/lib/utils";

export interface InputProps
  extends React.InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  error?: string;
  helperText?: string;
  icon?: React.ReactNode;
  variant?: "default" | "glass" | "minimal";
}

const Input = forwardRef<HTMLInputElement, InputProps>(
  ({ className, type, label, error, helperText, icon, variant = "default", ...props }, ref) => {
    const baseStyles = "w-full rounded-lg border px-4 py-3 text-sm transition-all duration-200 focus:outline-none focus:ring-2 disabled:opacity-50 disabled:cursor-not-allowed";
    
    const variants = {
      default: "border-slate-300/50 bg-white/80 backdrop-blur-sm focus:border-blue-500 focus:ring-blue-500/20 shadow-sm hover:shadow-md",
      glass: "border-white/20 bg-white/10 backdrop-blur-md focus:border-white/40 focus:ring-white/20 text-white placeholder:text-white/60",
      minimal: "border-0 bg-transparent focus:ring-2 focus:ring-blue-500/20 hover:bg-slate-50"
    };

    return (
      <div className="space-y-2">
        {label && (
          <label className="text-sm font-medium text-slate-700 dark:text-slate-300">
            {label}
          </label>
        )}
        <div className="relative">
          {icon && (
            <div className="absolute left-3 top-1/2 transform -translate-y-1/2 text-slate-400">
              {icon}
            </div>
          )}
          <input
            type={type}
            className={cn(
              baseStyles,
              variants[variant],
              icon ? "pl-10" : null,
              error ? "border-red-500 focus:border-red-500 focus:ring-red-500/20" : null,
              className
            )}
            ref={ref}
            {...props}
          />
        </div>
        {error && (
          <p className="text-sm text-red-600 dark:text-red-400">{error}</p>
        )}
        {helperText && !error && (
          <p className="text-sm text-slate-500 dark:text-slate-400">{helperText}</p>
        )}
      </div>
    );
  }
);

Input.displayName = "Input";

export { Input };
