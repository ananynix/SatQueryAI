import { useRef } from 'react';
import LandingHero from './components/LandingHero';
import GISConsole from './components/GISConsole';

function App() {
  const stageRef = useRef<HTMLDivElement>(null);

  const scrollToConsole = () => {
    document.getElementById('console')?.scrollIntoView({ behavior: 'smooth' });
  };

  const scrollToHero = () => {
    stageRef.current?.scrollTo({ top: 0, behavior: 'smooth' });
  };

  return (
    <div ref={stageRef} className="scroll-stage">
      <LandingHero onEngage={scrollToConsole} />
      <GISConsole onReturnToHero={scrollToHero} />
    </div>
  );
}

export default App;
