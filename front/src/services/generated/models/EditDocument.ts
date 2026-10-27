/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { EditAnchor } from './EditAnchor';
import type { EditClip } from './EditClip';
/**
 * A whole chapter's explicitly selected takes and retention declarations.
 */
export type EditDocument = {
    schema_version?: number;
    plan_id?: (string | null);
    title?: string;
    clips?: Array<EditClip>;
    anchors?: Array<EditAnchor>;
};

