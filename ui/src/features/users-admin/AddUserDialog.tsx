import { useState, type FormEvent } from "react";
import { Plus, UserPlus } from "lucide-react";
import { toast } from "sonner";
import { ApiError } from "@/api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useAddUser, type AdminRole } from "./hooks";

interface AddUserDialogProps {
  roles?: readonly AdminRole[];
}

function explain(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return fallback;
}

/**
 * Dialog wrapper around `POST /api/users`. Roles are populated from
 * `useRoles()` upstream when available; the fallback list mirrors the
 * built-ins shipped by the controller.
 */
export function AddUserDialog({ roles = [] }: AddUserDialogProps) {
  const [open, setOpen] = useState(false);
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("viewer");
  const [password, setPassword] = useState("");

  const add = useAddUser();

  const reset = () => {
    setUsername("");
    setEmail("");
    setRole("viewer");
    setPassword("");
  };

  const handleSubmit = (ev: FormEvent) => {
    ev.preventDefault();
    if (!username.trim()) return;
    add.mutate(
      {
        username: username.trim(),
        ...(email ? { email: email.trim() } : {}),
        role_slug: role,
        ...(password ? { password } : {}),
      },
      {
        onSuccess: () => {
          toast.success(`Created ${username.trim()}`);
          reset();
          setOpen(false);
        },
        onError: (err) =>
          toast.error(`Create failed: ${explain(err, "request failed")}`),
      },
    );
  };

  const roleOptions = roles.length
    ? roles.map((r) => ({ value: r.slug, label: r.name ?? r.slug }))
    : [
        { value: "admin", label: "admin" },
        { value: "operator", label: "operator" },
        { value: "viewer", label: "viewer" },
      ];

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) reset();
      }}
    >
      <DialogTrigger asChild>
        <Button variant="primary" size="sm" data-testid="add-user-trigger">
          <Plus aria-hidden />
          Add user
        </Button>
      </DialogTrigger>
      <DialogContent data-testid="add-user-dialog">
        <DialogHeader>
          <DialogTitle>Add user</DialogTitle>
          <DialogDescription>
            Creates a controller-side user. Plaintext passwords are never
            returned — leave the field blank to issue a reset ticket.
          </DialogDescription>
        </DialogHeader>
        <form
          className="flex flex-col gap-4"
          onSubmit={handleSubmit}
          aria-label="Add user"
        >
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="add-user-username">Username</Label>
            <Input
              id="add-user-username"
              autoComplete="off"
              required
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              data-testid="add-user-username"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="add-user-email">
              Email <span className="text-fg-faint">(optional)</span>
            </Label>
            <Input
              id="add-user-email"
              type="email"
              autoComplete="off"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              data-testid="add-user-email"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="add-user-role">Role</Label>
            <Select value={role} onValueChange={setRole}>
              <SelectTrigger
                id="add-user-role"
                data-testid="add-user-role-trigger"
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {roleOptions.map((o) => (
                  <SelectItem key={o.value} value={o.value}>
                    {o.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="add-user-password">
              Password <span className="text-fg-faint">(optional)</span>
            </Label>
            <Input
              id="add-user-password"
              type="password"
              autoComplete="new-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              data-testid="add-user-password"
            />
          </div>
          <DialogFooter>
            <DialogClose asChild>
              <Button type="button" variant="secondary">
                Cancel
              </Button>
            </DialogClose>
            <Button
              type="submit"
              variant="primary"
              loading={add.isPending}
              disabled={!username.trim()}
              data-testid="add-user-submit"
            >
              <UserPlus aria-hidden />
              Create
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
