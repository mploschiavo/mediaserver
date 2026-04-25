import type { Meta, StoryObj } from "@storybook/react";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "./card";
import { Button } from "./button";

const meta: Meta<typeof Card> = {
  title: "Atoms/Card",
  component: Card,
  parameters: { layout: "centered" },
};
export default meta;
type Story = StoryObj<typeof Card>;

export const Default: Story = {
  render: () => (
    <Card className="w-80">
      <CardHeader>
        <CardTitle>Reconciliation</CardTitle>
        <CardDescription>
          Drift detected across 3 services since the last sync.
        </CardDescription>
      </CardHeader>
      <CardContent className="text-sm text-fg-muted">
        Re-applying the desired state will recreate 2 containers.
      </CardContent>
      <CardFooter className="justify-end gap-2">
        <Button variant="ghost" size="sm">
          Dismiss
        </Button>
        <Button variant="primary" size="sm">
          Reconcile
        </Button>
      </CardFooter>
    </Card>
  ),
};

export const Bare: Story = {
  render: () => (
    <Card className="w-80 p-6 text-sm text-fg-muted">
      A bare card with arbitrary content. Use the slotted children only when
      you need the canonical header / footer rhythm.
    </Card>
  ),
};
