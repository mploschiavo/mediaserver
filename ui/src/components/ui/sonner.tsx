import { Toaster as Sonner, type ToasterProps } from "sonner";

/* Themed Sonner wrapper. Reads CSS vars rather than the OS toggle
   so the toast palette tracks our [data-theme] attribute. */
const Toaster = ({ ...props }: ToasterProps) => (
  <Sonner
    className="toaster group"
    toastOptions={{
      classNames: {
        toast:
          "group toast group-[.toaster]:bg-bg-1 group-[.toaster]:text-fg group-[.toaster]:border-border group-[.toaster]:shadow-lg group-[.toaster]:rounded-lg",
        description: "group-[.toast]:text-fg-muted",
        actionButton:
          "group-[.toast]:bg-accent group-[.toast]:text-accent-fg group-[.toast]:rounded-md",
        cancelButton:
          "group-[.toast]:bg-bg-2 group-[.toast]:text-fg-muted group-[.toast]:rounded-md",
      },
    }}
    {...props}
  />
);

export { Toaster };
