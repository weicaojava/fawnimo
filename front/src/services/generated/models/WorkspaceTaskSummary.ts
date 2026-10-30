/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Only generic state, real clocks and a server-built safe return target belong in the global panel.
 */
export type WorkspaceTaskSummary = {
    id: string;
    kind: 'agent_workflow' | 'model_connection_test' | 'video_understanding' | 'asset_image_generation' | 'script_candidate';
    title: string;
    status: 'pending' | 'running' | 'succeeded' | 'failed' | 'cancelled';
    progress?: (number | null);
    created_at_ts: (number | null);
    started_at_ts: (number | null);
    finished_at_ts: (number | null);
    return_path: string;
    return_note?: (string | null);
};

