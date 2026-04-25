import type { Preview } from "@storybook/react";
import { INITIAL_VIEWPORTS } from "@storybook/addon-viewport";
import "../src/styles/globals.css";

const preview: Preview = {
  parameters: {
    layout: "centered",
    controls: { expanded: true, matchers: { color: /(background|color)$/i } },
    backgrounds: { disable: true },
    // Mobile-first storybook: stories render at iPhone-15 width by
    // default so devs design for the smallest target before scaling up.
    viewport: {
      defaultViewport: "iphone15",
      viewports: {
        ...INITIAL_VIEWPORTS,
        iphone15: { name: "iPhone 15", styles: { width: "393px",  height: "852px"  }, type: "mobile" },
        pixel7:   { name: "Pixel 7",   styles: { width: "412px",  height: "915px"  }, type: "mobile" },
        ipadmini: { name: "iPad mini", styles: { width: "768px",  height: "1024px" }, type: "tablet" },
        mbp14:    { name: "MBP 14",    styles: { width: "1512px", height: "982px"  }, type: "desktop" },
      },
    },
  },
  globalTypes: {
    theme: {
      name: "Theme",
      description: "Active design-system theme",
      defaultValue: "dark",
      toolbar: {
        icon: "circlehollow",
        items: [
          { value: "dark", title: "Dark" },
          { value: "light", title: "Light" },
        ],
        dynamicTitle: true,
      },
    },
  },
  decorators: [
    (Story, context) => {
      const theme = context.globals.theme ?? "dark";
      if (typeof document !== "undefined") {
        document.documentElement.dataset.theme = theme;
      }
      return Story();
    },
  ],
};

export default preview;
