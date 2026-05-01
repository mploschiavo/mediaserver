import { describe, expect, it } from "vitest";
import {
  buildSetupExperienceState,
  humanizeStepLabel,
} from "./setupState";
import { SetupStatus } from "./setupStatusConstants";

describe("buildSetupExperienceState", () => {
  it("returns warming_up when controller is unreachable", () => {
    const state = buildSetupExperienceState({
      status: undefined,
      statusReachable: false,
      runningTree: [],
      history: [],
    });
    expect(state.phase).toBe(SetupStatus.WarmingUp);
    expect(state.title).toMatch(/reaching the controller/i);
    expect(state.timeline).toEqual([]);
  });

  it("returns queued while bootstrap has not completed and no running tree exists", () => {
    const state = buildSetupExperienceState({
      status: { initial_bootstrap_done: false },
      statusReachable: true,
      runningTree: [],
      history: [],
    });
    expect(state.phase).toBe(SetupStatus.Queued);
    expect(state.title).toMatch(/setting up/i);
    expect(state.isReady).toBe(false);
  });

  it("returns running with active bootstrap path and step summary from tree", () => {
    const state = buildSetupExperienceState({
      status: { initial_bootstrap_done: false },
      statusReachable: true,
      runningTree: [
        {
          run_id: "r1",
          job_name: "bootstrap",
          status: SetupStatus.Running,
          started_at: 100,
          elapsed_seconds: 50,
          triggered_by: "manual",
          actor: "",
          parent_run_id: "",
          batch_id: "",
          children: [
            {
              run_id: "r2",
              job_name: "configure_media_server",
              status: SetupStatus.Running,
              started_at: 110,
              elapsed_seconds: 40,
              triggered_by: "parent",
              actor: "",
              parent_run_id: "r1",
              batch_id: "r1",
              children: [],
            },
          ],
        },
      ],
      history: [],
    });
    expect(state.phase).toBe(SetupStatus.Running);
    expect(state.activePath).toEqual(["bootstrap", "configure_media_server"]);
    expect(state.activeStepLabel).toMatch(/media server/i);
    expect(state.summary.total).toBe(2);
    expect(state.summary.running).toBe(2);
    expect(state.timeline.length).toBe(2);
  });

  it("falls back to current_action when running tree is empty", () => {
    const state = buildSetupExperienceState({
      status: {
        initial_bootstrap_done: false,
        phase: SetupStatus.Running,
        current_action: {
          id: "a-12",
          name: "configure_sonarr",
          status: SetupStatus.Running,
          started_at: 10,
          elapsed_seconds: 30,
        },
        phases_completed: ["preflight", "prepare_host"],
      },
      statusReachable: true,
      runningTree: [],
      history: [],
    });
    expect(state.phase).toBe(SetupStatus.Running);
    expect(state.activeStepLabel).toBe("Configuring Sonarr");
    expect(state.summary.completed).toBe(2);
    expect(state.summary.running).toBe(1);
    expect(state.timeline.length).toBe(3);
    expect(state.timeline[2]?.label).toMatch(/sonarr/i);
  });

  it("returns failed when bootstrap history status is error", () => {
    const state = buildSetupExperienceState({
      status: { initial_bootstrap_done: true },
      statusReachable: true,
      runningTree: [],
      history: [
        { jobs: { bootstrap: { status: SetupStatus.Error } }, errors: 2 },
      ],
    });
    expect(state.phase).toBe(SetupStatus.Failed);
    expect(state.isReady).toBe(false);
    expect(state.summary.failed).toBeGreaterThanOrEqual(1);
  });

  it("returns complete on strict-ready success path", () => {
    const state = buildSetupExperienceState({
      status: {
        initial_bootstrap_done: true,
        phase: SetupStatus.Complete,
      },
      statusReachable: true,
      runningTree: [],
      history: [
        { jobs: { bootstrap: { status: SetupStatus.Ok } }, errors: 0 },
      ],
    });
    expect(state.phase).toBe(SetupStatus.Complete);
    expect(state.isReady).toBe(true);
    expect(state.title).toMatch(/ready/i);
  });
});

describe("humanizeStepLabel", () => {
  it("maps known step ids to friendly labels", () => {
    expect(humanizeStepLabel("configure_sonarr")).toBe("Configuring Sonarr");
    expect(humanizeStepLabel("discover_api_keys")).toBe("Discovering API keys");
    expect(humanizeStepLabel("prowlarr_seed_indexers")).toBe(
      "Loading indexer catalog",
    );
  });

  it("falls back to a title-cased version for unknown ids", () => {
    expect(humanizeStepLabel("custom_thing_name")).toBe("Custom thing name");
    expect(humanizeStepLabel("")).toBe("Working…");
  });
});
