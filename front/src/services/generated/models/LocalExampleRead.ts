/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Describe a stored original example without exposing paths, credentials or a generated-model provenance.
 */
export type LocalExampleRead = {
    project_id: string;
    chapter_id: string;
    script_snapshot_id: string;
    plan_id: string;
    draft_id: string;
    state?: string;
    source_kind?: string;
    clip_count?: number;
    picture_count?: number;
    duration_ms?: number;
    source_audio?: boolean;
    external_model_calls?: number;
    model_quality_qualified?: boolean;
    complete_film_qualified?: boolean;
    replayed: boolean;
    entry: string;
    notice: string;
};

