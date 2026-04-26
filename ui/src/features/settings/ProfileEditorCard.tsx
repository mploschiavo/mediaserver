import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { ApiError } from "@/api";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiErrorTile } from "@/components/ApiErrorTile";
import { formatRelative } from "@/features/media-integrity/format";
import { useProfileYaml, useSaveProfile, type ProfileResponse } from "./hooks";

function readYaml(p: ProfileResponse | undefined): string {
  if (!p) return "";
  if (typeof p.yaml === "string") return p.yaml;
  if (typeof p.content === "string") return p.content;
  return "";
}

function readSavedAt(p: ProfileResponse | undefined): string {
  if (!p) return "";
  if (typeof p.saved_at === "string") return p.saved_at;
  if (typeof p.updated_at === "string") return p.updated_at;
  return "";
}

function errMsg(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return fallback;
}

/**
 * Profile YAML editor. Renders the raw YAML in a `<textarea>` (mono
 * font, no syntax highlighting — server validates so the bundle
 * stays small). On save we POST and invalidate; on error we keep
 * the user's edits so nothing is lost.
 */
export function ProfileEditorCard() {
  const profile = useProfileYaml();
  const save = useSaveProfile();

  const initial = useMemo(() => readYaml(profile.data), [profile.data]);
  const [draft, setDraft] = useState<string>("");
  const [pristine, setPristine] = useState<boolean>(true);

  // Seed the draft from the server response once — and re-seed after
  // a successful save so the dirty flag clears.
  useEffect(() => {
    if (pristine) {
      setDraft(initial);
    }
  }, [initial, pristine]);

  const savedAt = readSavedAt(profile.data);
  const dirty = !pristine && draft !== initial;

  const handleSave = () => {
    if (save.isPending) return;
    save.mutate(
      { yaml: draft },
      {
        onSuccess: () => {
          toast.success("Profile saved");
          setPristine(true);
        },
        onError: (err) => {
          // Keep the user's edits — the toast surfaces the error and
          // the textarea retains the unsaved text.
          toast.error(errMsg(err, "Save failed"));
        },
      },
    );
  };

  return (
    <Card data-testid="profile-editor-card">
      <CardHeader>
        <CardTitle>Profile YAML</CardTitle>
        <CardDescription>
          Bootstrap profile. Saved server-side; the controller validates.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {profile.isLoading ? (
          <Skeleton
            className="h-64 w-full"
            data-testid="profile-editor-loading"
          />
        ) : profile.error ? (
          <ApiErrorTile
            error={profile.error}
            onRetry={() => void profile.refetch()}
          />
        ) : (
          <textarea
            aria-label="Profile YAML"
            data-testid="profile-editor-textarea"
            spellCheck={false}
            className="min-h-64 w-full resize-y rounded-md border border-input bg-bg-1 p-3 font-mono text-xs text-fg shadow-sm transition-colors focus-visible:border-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-bg"
            value={draft}
            onChange={(e) => {
              setPristine(false);
              setDraft(e.target.value);
            }}
          />
        )}
        <div className="flex items-center justify-between gap-3">
          <span
            className="text-xs text-fg-muted"
            data-testid="profile-editor-saved-at"
          >
            {savedAt
              ? `Last saved ${formatRelative(savedAt)}`
              : "Not yet saved"}
          </span>
          <Button
            variant="primary"
            onClick={handleSave}
            disabled={!dirty || save.isPending || profile.isLoading}
            loading={save.isPending}
            data-testid="profile-editor-save"
          >
            Save
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
