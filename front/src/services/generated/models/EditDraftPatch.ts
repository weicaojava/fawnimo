/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { EditDocument } from './EditDocument';
/**
 * Protect all editable fields with an expected revision.
 */
export type EditDraftPatch = {
    expected_revision: number;
    document: EditDocument;
};

