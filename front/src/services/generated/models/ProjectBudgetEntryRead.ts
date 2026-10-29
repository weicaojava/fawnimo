/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Expose one project's amount/state without remote identifiers, prompts or credentials.
 */
export type ProjectBudgetEntryRead = {
    job_id: string;
    project_id: string;
    state: 'reserved' | 'unknown' | 'released' | 'settled';
    quote_minor: number;
    actual_minor: (number | null);
    quote_minor_exact?: (string | null);
    actual_minor_exact?: (string | null);
    source?: 'durable_video' | 'model_call' | 'bill_adjustment' | 'unclassified';
    call_kind?: ('agent_text' | 'model_connection_test' | 'asset_image' | 'video_vision' | 'video_asr' | null);
};

