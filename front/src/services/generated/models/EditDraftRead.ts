/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { EditDocument } from './EditDocument';
import type { EditPublicationSummary } from './EditPublicationSummary';
import type { EditReadiness } from './EditReadiness';
/**
 * A complete controlled form and current checklist.
 */
export type EditDraftRead = {
    id: string;
    project_id: string;
    chapter_id: string;
    revision: number;
    document: EditDocument;
    readiness: EditReadiness;
    publications: Array<EditPublicationSummary>;
};

