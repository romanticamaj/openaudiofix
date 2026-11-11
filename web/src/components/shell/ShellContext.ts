import { createContext } from "react";

export const ShellContext = createContext<{ openNav: () => void }>({ openNav: () => {} });
