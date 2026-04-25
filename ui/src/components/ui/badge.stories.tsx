import type { Meta, StoryObj } from "@storybook/react";
import { Badge } from "./badge";

const meta: Meta<typeof Badge> = {
  title: "Atoms/Badge",
  component: Badge,
  parameters: { layout: "centered" },
  args: { children: "Healthy" },
  argTypes: {
    variant: {
      control: "select",
      options: ["default", "success", "warning", "danger", "info", "outline"],
    },
  },
};
export default meta;
type Story = StoryObj<typeof Badge>;

export const Default: Story = { args: { variant: "default" } };
export const Success: Story = { args: { variant: "success", children: "Healthy" } };
export const Warning: Story = { args: { variant: "warning", children: "Degraded" } };
export const Danger: Story = { args: { variant: "danger", children: "Failing" } };
export const Info: Story = { args: { variant: "info", children: "Reconciling" } };
export const Outline: Story = { args: { variant: "outline", children: "v1.42.0" } };

export const Gallery: Story = {
  render: () => (
    <div className="flex flex-wrap items-center gap-2">
      <Badge variant="default">default</Badge>
      <Badge variant="success">success</Badge>
      <Badge variant="warning">warning</Badge>
      <Badge variant="danger">danger</Badge>
      <Badge variant="info">info</Badge>
      <Badge variant="outline">outline</Badge>
    </div>
  ),
};
