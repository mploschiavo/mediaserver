import type { Meta, StoryObj } from "@storybook/react";
import { Play } from "lucide-react";
import { Button } from "./button";

const meta: Meta<typeof Button> = {
  title: "Atoms/Button",
  component: Button,
  parameters: { layout: "centered" },
  args: { children: "Reconcile now" },
  argTypes: {
    variant: {
      control: "select",
      options: ["default", "primary", "secondary", "ghost", "danger", "outline"],
    },
    size: { control: "select", options: ["sm", "md", "lg", "icon"] },
  },
};
export default meta;
type Story = StoryObj<typeof Button>;

export const Primary: Story = { args: { variant: "primary" } };
export const Secondary: Story = { args: { variant: "secondary" } };
export const Ghost: Story = { args: { variant: "ghost" } };
export const Danger: Story = { args: { variant: "danger" } };
export const Outline: Story = { args: { variant: "outline" } };
export const Loading: Story = { args: { loading: true, variant: "primary" } };
export const WithIcon: Story = {
  args: { variant: "primary" },
  render: (args) => (
    <Button {...args}>
      <Play />
      Reconcile now
    </Button>
  ),
};
