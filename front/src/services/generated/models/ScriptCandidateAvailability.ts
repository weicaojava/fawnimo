/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ExecutionModelChoice } from './ExecutionModelChoice';
/**
 * A source-bound fee preview does not reserve funds or issue any model request.
 */
export type ScriptCandidateAvailability = {
    available: boolean;
    message: string;
    source_revision: number;
    source_hash: string;
    input_characters: number;
    project_name: string;
    chapter_title: string;
    selected_model_id?: (string | null);
    selected_price_fingerprint?: (string | null);
    authorized_max_minor?: (string | null);
    models: Array<ExecutionModelChoice>;
};

