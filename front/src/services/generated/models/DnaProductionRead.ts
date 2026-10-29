/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Return stable imported identities, without implying extraction or generation readiness.
 */
export type DnaProductionRead = {
    id: string;
    project_id: string;
    draft_id: string;
    draft_revision: number;
    chapter_id: string;
    shot_ids: Array<string>;
    plan_draft_id: string;
    asset_bindings: Record<string, Record<string, any>>;
    prompt_template_ids: Array<string>;
    counts: Record<string, number>;
    status?: string;
    message?: string;
};

