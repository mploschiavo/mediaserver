import { useState } from "react";
import { motion, useReducedMotion } from "framer-motion";
import { Boxes, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { EmptyState } from "@/components/layout/EmptyState";
import { CustomServiceDialog } from "./CustomServiceDialog";

/**
 * Card surface for the custom-services feature. The OpenAPI spec
 * exposes only `POST /api/custom-service` today (no read endpoint), so
 * the card is a launcher into the Define dialog. When a `GET` lands,
 * the empty-state region can swap in a real list.
 */
export function CustomServiceCard() {
  const reduce = useReducedMotion();
  const [open, setOpen] = useState(false);

  return (
    <motion.div
      initial={reduce ? false : { opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.22, ease: [0.16, 1, 0.3, 1] }}
      data-testid="custom-services-card"
    >
      <Card>
        <CardHeader className="flex flex-row items-start justify-between gap-3 space-y-0">
          <div className="flex flex-col gap-1.5">
            <CardTitle className="flex items-center gap-2">
              <Boxes className="size-4 text-fg-muted" aria-hidden />
              Custom services
            </CardTitle>
            <CardDescription>
              Register a non-standard service so the controller can manage it
              alongside the built-ins.
            </CardDescription>
          </div>
          <Button
            variant="primary"
            size="sm"
            onClick={() => setOpen(true)}
            data-testid="custom-service-define-trigger"
          >
            <Plus aria-hidden />
            Define new
          </Button>
        </CardHeader>
        <CardContent>
          <EmptyState
            icon={Boxes}
            title="No custom services defined"
            description="Define one above. The controller registry holds it for the next provisioning pass."
          />
        </CardContent>
      </Card>
      <CustomServiceDialog open={open} onOpenChange={setOpen} />
    </motion.div>
  );
}
