/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { TransitionEdge } from './TransitionEdge';
/**
 * Only boundary choices are editable; source media, trim and adoptions stay fixed in the base.
 */
export type TransitionDocument = {
    schema_version?: number;
    title?: string;
    edges?: Array<TransitionEdge>;
};

