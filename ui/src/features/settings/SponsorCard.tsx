import { Heart } from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

const PAYPAL_DONATE_URL =
  "https://www.paypal.com/donate?hosted_button_id=XKDG7XXVEQK3W";

export function SponsorCard() {
  return (
    <Card data-testid="sponsor-card">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Heart aria-hidden className="size-4 text-danger" />
          Support the project
        </CardTitle>
        <CardDescription>
          Media Stack is built and maintained on personal time. Donations
          help keep the controller image building, the docs current, and
          new services landing.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <a
          href={PAYPAL_DONATE_URL}
          target="_blank"
          rel="noreferrer noopener"
          data-testid="sponsor-paypal-link"
          className="inline-flex"
          title="PayPal — secure donation"
        >
          <img
            src="https://www.paypalobjects.com/en_US/i/btn/btn_donateCC_LG.gif"
            alt="Donate with PayPal"
            width={147}
            height={47}
            loading="lazy"
          />
        </a>
      </CardContent>
    </Card>
  );
}
