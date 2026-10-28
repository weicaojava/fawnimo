/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DnaAnalysisOption } from './DnaAnalysisOption';
import type { DnaDraftOption } from './DnaDraftOption';
import type { DnaVersionOption } from './DnaVersionOption';
/**
 * Actual bounded options; oversized projects fail rather than silently truncate.
 */
export type DnaCatalog = {
    analyses: Array<DnaAnalysisOption>;
    drafts: Array<DnaDraftOption>;
    versions: Array<DnaVersionOption>;
};

