/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * A failed source remains explicit so clients keep its last-known items instead of silently showing zero.
 */
export type WorkspaceTaskSource = {
    kind: 'agent_workflow' | 'model_connection_test' | 'video_understanding' | 'asset_image_generation' | 'script_candidate';
    available: boolean;
    message: string;
};

