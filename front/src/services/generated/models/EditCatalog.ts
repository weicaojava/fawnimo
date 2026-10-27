/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { EditCandidateOption } from './EditCandidateOption';
import type { EditClipOption } from './EditClipOption';
import type { EditItemOption } from './EditItemOption';
import type { EditMediaOption } from './EditMediaOption';
import type { EditPlanOption } from './EditPlanOption';
import type { EditSelectionOption } from './EditSelectionOption';
/**
 * Bounded real chapter choices; limits fail rather than truncate.
 */
export type EditCatalog = {
    project_id: string;
    chapter_id: string;
    plans: Array<EditPlanOption>;
    plan_id: (string | null);
    clips: Array<EditClipOption>;
    items: Array<EditItemOption>;
    selections: Array<EditSelectionOption>;
    media: Array<EditMediaOption>;
    candidates: Array<EditCandidateOption>;
};

